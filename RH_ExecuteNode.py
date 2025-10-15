import requests
import time
import json
from PIL import Image, ImageDraw, ImageFont
from io import BytesIO
import numpy as np
import torch
import os
import websocket  # Requires websocket-client package
import threading
import comfy.utils # Import comfy utils for ProgressBar
import cv2 # <<< Added import for OpenCV
import safetensors.torch # <<< Added safetensors import
import torchaudio 
import torch.nn.functional as F # <<< Add F for padding

# Try importing ComfyUI video classes safely
try:
    # Use the correct official ComfyUI import path
    from comfy_api.input_impl import VideoFromFile
    video_support_available = True
    print("ComfyUI video support loaded successfully.")
except ImportError:
    try:
        # Alternative import paths for different ComfyUI versions
        from comfy_execution.graph_utils import VideoFromFile
        video_support_available = True
        print("ComfyUI video support loaded via alternative path.")
    except ImportError:
        try:
            from comfy.graph_utils import VideoFromFile
            video_support_available = True
            print("ComfyUI video support loaded via legacy path.")
        except ImportError:
            # Fallback if video support not available
            video_support_available = False
            print("Warning: ComfyUI video support not available. VIDEO output will return None.")

# Try importing folder_paths safely
try:
    import folder_paths
    comfyui_env_available = True # Use a more generic name
except ImportError:
    comfyui_env_available = False
    print("ComfyUI folder_paths not found. Some features like specific output paths might use fallbacks.")


class ExecuteNode:
    ESTIMATED_TOTAL_NODES = 10 # Default estimate

    def __init__(self):
        self.ws = None
        self.task_completed = False
        self.ws_error = None
        self.executed_nodes = set()
        self.prompt_tips = "{}"
        self.pbar = None
        self.node_lock = threading.Lock()
        self.total_nodes = None
        self.current_steps = 0 # Track current steps for logging

    def update_progress(self):
        """Increments the progress bar by one step and logs, stopping at total_nodes."""
        with self.node_lock:
            # Guard 1: Check completion status first
            if self.task_completed:
                # Optional: Log if needed, but return silently to avoid spam
                # print(f"Skipping progress update because task is already completed.")
                return

            # Guard 2: Check if progress bar exists AND if we are already at or beyond the total
            if not self.pbar or self.current_steps >= self.total_nodes:
                 # Optional: Log if trying to update when already >= total for debugging
                 # if self.pbar and self.current_steps >= self.total_nodes:
                 #     print(f"Debug: update_progress called when steps ({self.current_steps}) >= total ({self.total_nodes}). Skipping update.")
                 return

            # --- If guards passed, proceed with increment and update --- 
            self.current_steps += 1
            # Increment the ComfyUI progress bar by 1
            self.pbar.update(1)
            # Log the current state
            # Use min for logging safety, although current_steps should now never exceed total_nodes here
            display_steps = min(self.current_steps, self.total_nodes) 
            print(f"Progress Update: Step {display_steps}/{self.total_nodes} ({(display_steps/self.total_nodes)*100:.1f}%)")


    def complete_progress(self):
        """Sets the progress bar to 100% and marks task as completed."""
        # --- Use lock for thread safety ---
        with self.node_lock:
            # Check if already completed to prevent redundant calls/logs
            if self.task_completed:
                return

            print(f"Finalizing progress: Setting task_completed = True")
            # --- Set completion flag FIRST ---
            self.task_completed = True

            # --- Update progress bar to final state --- 
            if self.pbar:
                # Ensure the bar visually reaches 100% regardless of intermediate steps received
                print(f"Forcing progress bar to 100% ({self.total_nodes}/{self.total_nodes}). Current steps internally were {self.current_steps}.")
                # Use update_absolute to set the final value and total explicitly.
                # This handles cases where it finished early or exactly on time.
                self.pbar.update_absolute(self.total_nodes, self.total_nodes)
                # Also update internal counter for consistency, although it might be redundant now
                self.current_steps = self.total_nodes
            else:
                 print("Progress bar not available during finalization.")


    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "apiConfig": ("STRUCT",),
            },
            "optional": {
                "nodeInfoList": ("ARRAY", {"default": []}),
                "run_timeout": ("INT", {"default": 600, "min": 1, "max": 9999999}), # Corrected comma and added closing brace
                "concurrency_limit": ("INT", {"default": 1, "min": 1, "max": 100}), # Restored min/max
                "is_webapp_task": ("BOOLEAN", {"default": False}),
                "use_rtx4090_48g": ("BOOLEAN", {"default": False}),
            },
        }

    RETURN_TYPES = ("IMAGE", "IMAGE", "LATENT", "STRING", "AUDIO", "VIDEO", "VIDEO", "VIDEO", "VIDEO", "VIDEO")
    RETURN_NAMES = ("images", "video_frames", "latent", "text", "audio", "video1", "video2", "video3", "video4", "video5")

    CATEGORY = "RunningHub"
    FUNCTION = "process"
    OUTPUT_NODE = True # Indicate support for progress display

    # --- WebSocket Handlers ---
    def on_ws_message(self, ws, message):
        """Handle WebSocket messages and update internal state and progress bar"""
        try:
            # Check completion status AT THE START
            with self.node_lock:
                is_completed = self.task_completed
            if is_completed:
                 # print("WS Message received after task completion, ignoring.") # Optional: reduce log spam
                 return

            # --- Safely handle message decoding and JSON parsing ---
            print(f"--- Raw WS Message Received ---")
            
            # Handle different message types (string, bytes, etc.)
            processed_message = None
            if isinstance(message, bytes):
                # Try different encodings for bytes
                for encoding in ['utf-8', 'utf-16', 'latin-1']:
                    try:
                        processed_message = message.decode(encoding)
                        print(f"Successfully decoded bytes message using {encoding}")
                        break
                    except UnicodeDecodeError:
                        continue
                
                if processed_message is None:
                    print(f"Warning: Could not decode bytes message with any common encoding. Raw bytes length: {len(message)}")
                    print(f"First 50 bytes (hex): {message[:50].hex() if len(message) >= 50 else message.hex()}")
                    return # Skip this message
            elif isinstance(message, str):
                processed_message = message
            else:
                print(f"Warning: Received unknown message type: {type(message)}")
                return

            # Try to parse as JSON
            data = None
            try:
                data = json.loads(processed_message)
                print(json.dumps(data, indent=2, ensure_ascii=False))
            except json.JSONDecodeError as e:
                print(f"Warning: Could not parse message as JSON: {e}")
                print(f"Raw message content (first 200 chars): {processed_message[:200]}")
                print(f"Message length: {len(processed_message)}")
                
                # Try to extract any JSON-like content if it's mixed with other data
                try:
                    # Look for JSON-like patterns in the message
                    import re
                    json_match = re.search(r'\{.*\}', processed_message, re.DOTALL)
                    if json_match:
                        potential_json = json_match.group(0)
                        data = json.loads(potential_json)
                        print("Successfully extracted JSON from mixed content")
                        print(json.dumps(data, indent=2, ensure_ascii=False))
                    else:
                        print("No JSON pattern found in message, skipping...")
                        return
                except Exception as extract_e:
                    print(f"Failed to extract JSON from message: {extract_e}")
                    return
                    
            print(f"-----------------------------")
            # --- End safe message processing ---

            if data is None:
                print("No valid data extracted from WebSocket message")
                return
            message_type = data.get("type")
            node_data = data.get("data", {})
            node_id = node_data.get("node")

            # Handle node execution updates (both 'executing' and 'execution_success')
            # Based on user feedback, 'execution_success' might signal single node completion.
            if message_type == "executing" or message_type == "execution_success":
                if node_id is not None:
                    # Check if it's a new node before calling update
                    # Use lock to safely check and add to executed_nodes
                    with self.node_lock:
                         is_new_node = node_id not in self.executed_nodes
                         if is_new_node:
                             self.executed_nodes.add(node_id)
                    
                    if is_new_node:
                         self.update_progress() # This method is guarded internally
                         print(f"WS ({message_type}): Node {node_id} reported.")
                    else:
                         print(f"WS ({message_type}): Node {node_id} reported again (ignored for progress).")
                elif message_type == "executing" and node_id is None: # Null node signal
                    print("WS (executing): Received null node signal, potentially end of execution phase.")
                elif message_type == "execution_success" and node_id is None:
                    # If execution_success doesn't have a node_id, what does it mean?
                    # Log it for now, DO NOT call complete_progress.
                    print(f"WS (execution_success): Received signal without node_id. Data: {node_data}")
                    # self.complete_progress() # <<< REMOVED - This was incorrect based on user feedback

            # Handle other message types if necessary (e.g., specific overall error messages)
            # elif message_type == "execution_error": # Hypothetical example
            #     error_details = node_data.get("error", "Unknown WS error")
            #     print(f"WS: Received execution error: {error_details}")
            #     with self.node_lock:
            #         if not self.task_completed:
            #             if self.ws_error is None:
            #                 self.ws_error = Exception(f"WS Error: {error_details}")
            #             self.task_completed = True
            
            else:
                 print(f"WS: Received unhandled message type '{message_type}': {data}")

        except UnicodeDecodeError as e:
            print(f"Error: WebSocket message encoding issue: {e}")
            print("This is likely a non-critical WebSocket protocol issue. Continuing task...")
            # Don't set error state for encoding issues - these are usually non-critical
            # and the task can continue via HTTP polling
            
        except json.JSONDecodeError as e:
            print(f"Error: WebSocket message JSON parsing issue: {e}")
            print("This is likely a non-critical WebSocket protocol issue. Continuing task...")
            # Don't set error state for JSON parsing issues - these are usually non-critical
            
        except Exception as e:
            print(f"Error processing WebSocket message: {e}")
            print(f"Exception type: {type(e).__name__}")
            
            # Only set error state for critical exceptions
            if isinstance(e, (ConnectionError, OSError, IOError)):
                print("Critical WebSocket error detected, marking as error state")
                with self.node_lock:
                    if not self.task_completed:
                        if self.ws_error is None:
                            self.ws_error = e
                        # Don't necessarily mark completed here, let polling confirm final state
                        # self.task_completed = True
            else:
                print("Non-critical WebSocket error, continuing task via HTTP polling...") 

    def on_ws_error(self, ws, error):
        """Handle WebSocket errors"""
        print(f"WebSocket error: {error}")
        # Don't set ws_error for connection errors - let HTTP polling continue
        # Only log the error and continue monitoring via HTTP
        error_str = str(error).lower()
        if "connection" in error_str or "lost" in error_str:
            print("WebSocket connection lost. Switching to HTTP polling mode...")
            # Don't mark as error or complete - let HTTP polling continue
        else:
            # For other critical errors, still set the error
            self.ws_error = error
            self.complete_progress()

    def on_ws_close(self, ws, close_status_code, close_msg):
        """Handle WebSocket close"""
        print(f"WebSocket closed: {close_status_code} - {close_msg}")
        # If closed unexpectedly, don't immediately fail - let HTTP polling continue
        # Use lock temporarily just to read task_completed safely
        with self.node_lock:
             should_complete = not self.task_completed
        if should_complete:
             print("Warning: WebSocket closed unexpectedly. Continuing with HTTP polling mode...")
             # Don't set error or force completion - let HTTP polling handle task monitoring
             # The polling mechanism will detect if the task actually failed or succeeded

    def on_ws_open(self, ws):
        """Handle WebSocket connection open"""
        print("WebSocket connection established")
        # Note: executed_nodes should be cleared at the start of 'process'

    def connect_websocket(self, wss_url):
        """Establish WebSocket connection"""
        print(f"Connecting to WebSocket: {wss_url}")
        websocket.enableTrace(False) # Keep this false unless debugging WS protocol
        self.ws = websocket.WebSocketApp(
            wss_url,
            on_message=self.on_ws_message,
            on_error=self.on_ws_error,
            on_close=self.on_ws_close,
            on_open=self.on_ws_open
        )
        ws_thread = threading.Thread(target=self.ws.run_forever, name="RH_ExecuteNode_WSThread")
        ws_thread.daemon = True
        ws_thread.start()
        print("WebSocket thread started.")

    def check_and_complete_task(self):
        """If task times out after null node, force completion."""
        # complete_progress now checks the flag internally and uses lock
        print("Task completion timeout after null node signal - attempting forced completion.")
        self.complete_progress()

    def get_workflow_node_count(self, api_key, base_url, workflow_id):
        """Get the total number of nodes from workflow JSON."""
        url = f"{base_url}/api/openapi/getJsonApiFormat"
        headers = {
            "Content-Type": "application/json",
            "User-Agent": "ComfyUI-RH-APICall-Node/1.0",
        }
        data = {
            "apiKey": api_key,
            "workflowId": workflow_id
        }

        max_retries = 5
        retry_delay = 1
        last_exception = None
        node_count = None

        for attempt in range(max_retries):
            response = None
            try:
                print(f"Attempt {attempt + 1}/{max_retries} to get workflow node count...")
                response = requests.post(url, json=data, headers=headers, timeout=30)
                response.raise_for_status()

                result = response.json()

                if result.get("code") != 0:
                    api_msg = result.get('msg', 'Unknown API error')
                    print(f"API error on attempt {attempt + 1}: {api_msg}")
                    raise Exception(f"API error getting workflow node count: {api_msg}")

                workflow_json = result.get("data", {}).get("prompt")
                if not workflow_json:
                    raise Exception("No workflow data found in response")

                # Parse the workflow JSON
                workflow_data = json.loads(workflow_json)
                
                # Count the number of nodes
                node_count = len(workflow_data)
                print(f"Workflow contains {node_count} nodes")
                return node_count

            except (requests.exceptions.RequestException, json.JSONDecodeError, ValueError, Exception) as e:
                print(f"Error on attempt {attempt + 1}/{max_retries}: {e}")
                last_exception = e
                if isinstance(e, json.JSONDecodeError) and response is not None:
                     print(f"Raw response text on JSON decode error: {response.text}")

                if attempt < max_retries - 1:
                    print(f"Retrying in {retry_delay} seconds...")
                    time.sleep(retry_delay)
                    retry_delay *= 2
                else:
                    print("Max retries reached for getting workflow node count.")
                    raise Exception(f"Failed to get workflow node count after {max_retries} attempts. Last error: {last_exception}") from last_exception

        # This should ideally not be reached if the loop logic is correct
        raise Exception(f"Failed to get workflow node count after {max_retries} attempts (unexpected loop end). Last error: {last_exception}")

    # --- Main Process Method ---
    def process(self, apiConfig, nodeInfoList=None, run_timeout=600, concurrency_limit=1, is_webapp_task=False, use_rtx4090_48g=False):
        # Reset state
        with self.node_lock: # Use lock for resetting shared state
            self.executed_nodes.clear()
            self.task_completed = False
            self.ws_error = None
            self.prompt_tips = "{}"
            self.current_steps = 0 # Reset step counter

        # Get config values
        api_key = apiConfig.get("apiKey")
        base_url = apiConfig.get("base_url")

        if not api_key or not base_url:
            raise ValueError("Missing required apiConfig fields: apiKey, base_url")

        # Get workflow node count from API (only for non-AI App tasks)
        self.total_nodes = self.ESTIMATED_TOTAL_NODES # Default
        retrieved_workflow_id = apiConfig.get("workflowId_webappId") # <<< Changed key here

        if not is_webapp_task:
            # --- Standard ComfyUI Task --- 
            print("Standard ComfyUI Task mode enabled.")
            try:
                # workflow_id = apiConfig.get("workflowId_webappId") # Already retrieved
                if not retrieved_workflow_id:
                    print("Warning: workflowId_webappId missing in apiConfig for standard task. Using default node estimate.")
                    # Fall through to use default estimate
                else:
                    # Get actual node count from workflow
                    actual_node_count = self.get_workflow_node_count(api_key, base_url, retrieved_workflow_id)
                    # Use the actual node count directly
                    self.total_nodes = actual_node_count
                    print(f"Using actual total nodes for progress: {self.total_nodes}")
            except Exception as e:
                print(f"Error getting workflow node count, using default value: {e}")
                # self.total_nodes is already set to default
                print(f"Using default total nodes for progress: {self.total_nodes}")
        else:
            # --- AI App Task --- 
            # Rename print log message to reflect webapp task
            print(f"Webapp Task mode enabled. Using default estimated nodes for progress: {self.total_nodes}")
            # Validate that workflowId (acting as webappId) is provided in config
            if not retrieved_workflow_id:
                 # Update ValueError message
                 raise ValueError("workflowId_webappId (acting as webappId) must be provided in apiConfig when is_webapp_task is True.")
            # Optional: Add validation if webappId must be numeric, though API might handle string conversion
            try:
                # Attempt conversion to int, but keep it as string for the API call if needed
                int(retrieved_workflow_id)
                # Update print log message
                print(f"Using workflowId_webappId from apiConfig as webappId: {retrieved_workflow_id}")
            except ValueError:
                 # Update print log message
                 print(f"Warning: workflowId_webappId '{retrieved_workflow_id}' provided for Webapp Task is not purely numeric, but proceeding.")


        # Initialize ComfyUI progress bar
        self.pbar = comfy.utils.ProgressBar(self.total_nodes)
        print("Progress bar initialized at 0")

        # --- Concurrency Check ---
        # api_key and base_url are already validated
        try:
            account_status = self.check_account_status(api_key, base_url)
            current_tasks = int(account_status["currentTaskCounts"])
            print(f"There are {current_tasks} tasks running")
            
            if current_tasks >= concurrency_limit:  
                print(f"Concurrency limit ({concurrency_limit}) reached, waiting...")
                start_wait_time = time.time()
                # Use a shorter sleep interval while waiting for concurrency
                wait_interval = 2 # seconds
                while current_tasks >= concurrency_limit:
                    if time.time() - start_wait_time > run_timeout:
                        if self.pbar: self.pbar.update_absolute(1.0) # Use absolute directly for setup failure
                        raise Exception(f"Timeout waiting for concurrent tasks ({current_tasks}/{concurrency_limit}) to finish.")
                    print(f"Waiting for concurrent tasks... ({current_tasks}/{concurrency_limit})")
                    time.sleep(wait_interval) 
                    account_status = self.check_account_status(api_key, base_url)
                    current_tasks = int(account_status["currentTaskCounts"])
                print("Concurrency slot available.")
        except Exception as e:
             print(f"Error checking account status or waiting: {e}")
             if self.pbar: self.pbar.update_absolute(1.0) # Use absolute directly for setup failure
             raise

        # --- Task Creation & WebSocket ---
        task_id = None
        wss_url = None # <<< Initialize wss_url
        try:
            print(f"ExecuteNode NodeInfoList: {nodeInfoList}")

            # <<< Decide which creation function to call >>>
            if is_webapp_task:
                # Call AI App Task creation, passing the retrieved ID as webappId
                webappId_to_pass = retrieved_workflow_id # Use the ID from config
                # Update print log message
                print(f"Creating Webapp task with webappId: {webappId_to_pass}...")
                task_creation_result = self.create_ai_app_task(apiConfig, nodeInfoList or [], webappId_to_pass, use_rtx4090_48g)
            else:
                # Call standard ComfyUI Task creation
                print("Creating standard ComfyUI task...")
                # <<< Add base_url back to the create_task call >>>
                task_creation_result = self.create_task(apiConfig, nodeInfoList or [], base_url, use_rtx4090_48g)

            print(f"Task Creation Result: {json.dumps(task_creation_result, indent=2, ensure_ascii=False)}")

            # Validate task creation response structure before accessing data
            if not isinstance(task_creation_result.get("data"), dict):
                 raise ValueError("Invalid task creation response data structure.")

            task_data = task_creation_result["data"]
            self.prompt_tips = task_data.get("promptTips", "{}")
            task_id = task_data.get("taskId")
            initial_status = task_data.get("taskStatus")
            wss_url = task_data.get("netWssUrl") # <<< Get initial WSS URL

            if not task_id:
                 raise ValueError("Missing taskId in task creation response.")

            print(f"Task created, taskId: {task_id}, Initial Status: {initial_status}")

            # --- Handle QUEUED state ---
            if initial_status == "QUEUED" and not wss_url:
                print("Task is QUEUED. Polling for RUNNING status and WebSocket URL...")
                queue_start_time = time.time()
                poll_interval = 2 # seconds
                while True:
                    # Check timeout while waiting in queue
                    if time.time() - queue_start_time > run_timeout:
                         raise TimeoutError(f"Timeout waiting for task {task_id} to leave QUEUED state.")

                    # Check task status
                    status_result = self.check_task_status(task_id, api_key, base_url)
                    current_status = status_result.get("taskStatus")
                    print(f"  Polling status for queued task {task_id}: {current_status}")

                    if current_status == "RUNNING":
                        # Task is running, try to get WSS URL from status check
                        wss_url = status_result.get("netWssUrl")
                        if wss_url:
                            print(f"Task {task_id} is RUNNING. WebSocket URL obtained: {wss_url}")
                            break # Exit queue polling loop
                        else:
                            # This case might indicate an API design issue or a transient state
                            print(f"Warning: Task {task_id} is RUNNING but WebSocket URL not yet available from status check. Retrying check...")
                            # Keep polling, maybe the URL will appear shortly
                    elif current_status == "error":
                         error_msg = status_result.get('error', 'Unknown error during queue polling')
                         raise Exception(f"Task {task_id} failed while in queue: {error_msg}")
                    elif isinstance(status_result, list): # Task completed while polling queue status
                         print(f"Task {task_id} completed while polling queue status. Skipping WebSocket connection.")
                         # Set wss_url to a non-None dummy value to skip connection attempt later
                         wss_url = "skipped_completed_in_queue" 
                         break # Exit queue polling loop
                    elif current_status != "QUEUED":
                        # Handle unexpected status if necessary
                        print(f"Warning: Task {task_id} transitioned to unexpected status '{current_status}' while polling queue.")
                        # Decide if we should break or continue polling based on the status

                    # Wait before next poll
                    time.sleep(poll_interval)

            # --- Connect WebSocket if URL is available and not skipped ---
            if wss_url and wss_url != "skipped_completed_in_queue":
                print(f"Attempting to connect WebSocket: {wss_url}")
                self.connect_websocket(wss_url)
            elif not wss_url:
                # If still no WSS URL after potential polling (e.g., finished directly, or RUNNING but no URL provided)
                # Raise error or proceed without WS? Let's raise error for now.
                raise ValueError(f"Failed to obtain WebSocket URL for task {task_id} after creation/polling.")
            else: # wss_url == "skipped_completed_in_queue"
                 print("WebSocket connection skipped as task already completed.")

        except Exception as e:
             print(f"Error during task creation, queue polling, or WS connection: {e}")
             if self.pbar: self.pbar.update_absolute(1.0) # Use absolute directly for setup failure
             raise

        # --- Task Monitoring Loop ---
        task_start_time = time.time()
        loop_sleep_interval = 0.1 # Short sleep for responsiveness
        poll_status_interval = 5 # Poll HTTP status every 5 seconds
        last_poll_time = time.time() # Track last poll time
        print("Starting task monitoring loop...")

        timeout_timer = None
        final_error = None # <<< Define final_error outside try/finally

        try:
            # Setup global timeout timer
            def force_timeout():
                # Use lock to safely check task_completed
                with self.node_lock:
                     is_completed = self.task_completed
                if not is_completed:
                    print("Global timeout reached - forcing task completion.")
                    # Use lock to set error safely
                    with self.node_lock:
                        # Check if ws_error is already set to avoid overwriting a more specific WS error
                        if self.ws_error is None:
                             self.ws_error = Exception("Global timeout reached")
                        # Just set the flags here to break loop
                        self.task_completed = True # Set flag directly here to break loop

            timeout_timer = threading.Timer(run_timeout, force_timeout)
            timeout_timer.daemon = True
            timeout_timer.start()

            # Main wait loop
            while True: # <<< Modified loop structure
                # 1. Check completion flags (set by WS handlers or polling)
                with self.node_lock:
                     is_completed = self.task_completed
                     current_error = self.ws_error
                if is_completed or current_error:
                     print(f"Loop Exit: Task Completed={is_completed}, Error Present={current_error is not None}")
                     break # Exit loop if completed or error occurred via WS or polling

                # 2. Check for global timeout explicitly
                if time.time() - task_start_time > run_timeout:
                     print("Task monitoring loop timeout check triggered.")
                     with self.node_lock:
                         if not self.task_completed: # Avoid overwriting specific error
                            if self.ws_error is None:
                                self.ws_error = Exception(f"Timeout: Task {task_id} did not complete within {run_timeout} seconds.")
                            self.task_completed = True # Ensure loop exit
                     break # Exit loop

                # 3. Periodic HTTP Status Polling (Robustness check)
                current_time = time.time()
                if current_time - last_poll_time >= poll_status_interval:
                    print(f"Polling HTTP status for task {task_id}...")
                    last_poll_time = current_time # Update last poll time
                    try:
                        # Call check_task_status (requires api_key, base_url)
                        status_result = self.check_task_status(task_id, api_key, base_url)

                        # Analyze polling result
                        if isinstance(status_result, list): # Task completed successfully
                             print(f"Polling detected task {task_id} completed successfully.")
                             # Use lock to set flags safely
                             with self.node_lock:
                                 if not self.task_completed: # Avoid redundant completion if WS already handled it
                                     self.task_completed = True
                                     # No need to set ws_error if successful
                             # Loop will break on next iteration due to task_completed flag
                        elif isinstance(status_result, dict):
                            polled_status = status_result.get("taskStatus")
                            if polled_status == "error":
                                error_msg = status_result.get('error', 'Unknown error reported by polling')
                                print(f"Polling detected task {task_id} failed: {error_msg}")
                                # Use lock to set flags safely
                                with self.node_lock:
                                     if not self.task_completed: # Check completion first
                                         # Set error only if no other error is already present
                                         if self.ws_error is None:
                                             self.ws_error = Exception(f"Task failed (polled): {error_msg}")
                                         self.task_completed = True # Mark as complete to exit loop
                                # Loop will break on next iteration
                            elif polled_status == "completed_no_output":
                                print(f"Polling detected task {task_id} completed with no output. Setting completion flag.")
                                # Use lock to set flags safely
                                with self.node_lock:
                                     if not self.task_completed: # Check completion first
                                         # Set specific error for no output case
                                         if self.ws_error is None:
                                             self.ws_error = Exception("Task completed successfully but the workflow produced no output results. Possible reasons: 1) Workflow is configured to execute but has no output nodes; 2) Output nodes are disabled; 3) Workflow logic resulted in no final output")
                                         self.task_completed = True # Mark as complete to exit loop
                                # Loop will break on next iteration
                            elif polled_status in ["RUNNING", "QUEUED"]:
                                print(f"Polling: Task {task_id} is still {polled_status}.")
                                # Optionally check for netWssUrl again if needed, but primary goal is status check
                            else:
                                print(f"Polling: Received unexpected status '{polled_status}' for task {task_id}.")
                        else:
                             print(f"Polling: Received unexpected result type for task {task_id}: {type(status_result)}")

                    except Exception as poll_e:
                        # Don't necessarily stop the whole process on a single polling error,
                        # maybe it's transient. Log it. WS might still be active.
                        print(f"Warning: Error during periodic status polling for task {task_id}: {poll_e}")
                        # Consider adding a counter to stop polling after too many errors?

                # 4. Yield CPU
                time.sleep(loop_sleep_interval)

            # Handle exit conditions after loop
            with self.node_lock: # Read error flag safely
                 final_error = self.ws_error # Assign to outer scope variable

            if final_error:
                print(f"Task ended with error: {final_error}")
                # complete_progress handles internal checks and ensures final state
                self.complete_progress()
            else: # Task completed normally (either via WS or polling success)
                print("Task monitoring completed successfully.")
                # Ensure completion is marked, even if WS didn't send success or polling found success
                self.complete_progress()

        finally: # <<< Existing finally clause remains
            # Cleanup
            if timeout_timer:
                timeout_timer.cancel()
            if self.ws:
                try:
                    self.ws.close()
                except Exception as e:
                    print(f"Error closing WebSocket: {e}")
                self.ws = None

            # Final safety net: Ensure progress is marked complete.
            self.complete_progress()

        # If an error occurred during the loop, raise it now after cleanup
        if final_error:
            raise final_error

        # --- Process Output ---
        print("Processing task output...")
        # Pass the validated api_key and base_url again
        return self.process_task_output(task_id, api_key, base_url)

    def process_task_output(self, task_id, api_key, base_url):
        """Handles task output, separating images, video frames, audio, etc."""
        max_retries = 30
        retry_interval = 1
        max_retry_interval = 5
        image_data_list = [] # <<< For regular images
        frame_data_list = [] # <<< For video frames
        latent_data = None
        text_data = None
        audio_data = None # <<< For audio data
        
        # Track consecutive empty results to detect completed tasks with no output
        consecutive_empty_results = 0
        max_consecutive_empty = 3  # If we get 3 consecutive empty results, assume task completed with no output

        for attempt in range(max_retries):
            task_status_result = None
            try:
                task_status_result = self.check_task_status(task_id, api_key, base_url)
                print(f"Check output attempt {attempt + 1}/{max_retries}")

                # Handle completed task with no output - immediate exception
                if isinstance(task_status_result, dict) and task_status_result.get("taskStatus") == "completed_no_output":
                    raise Exception("Task completed successfully but the workflow produced no output results. Possible reasons: 1) Workflow is configured to execute but has no output nodes; 2) Output nodes are disabled; 3) Workflow logic resulted in no final output")

                if isinstance(task_status_result, dict) and task_status_result.get("taskStatus") in ["RUNNING", "QUEUED"]:
                    # Check if this is repeated RUNNING status (should be rare now with better detection)
                    if task_status_result.get("taskStatus") == "RUNNING":
                        consecutive_empty_results += 1
                        print(f"Task status RUNNING with no output data (attempt {consecutive_empty_results}/{max_consecutive_empty})")
                        
                        if consecutive_empty_results >= max_consecutive_empty:
                            # Multiple consecutive RUNNING status with no data suggests completed task with no output (backup detection)
                            raise Exception("Task completed successfully but the workflow produced no output results. Possible reasons: 1) Workflow is configured to execute but has no output nodes; 2) Output nodes are disabled; 3) Workflow logic resulted in no final output")
                    else:
                        # For QUEUED status, reset counter as it's a different state
                        consecutive_empty_results = 0
                    
                    wait_time = min(retry_interval * (1.5 ** attempt), max_retry_interval)
                    print(f"Task still running ({task_status_result.get('taskStatus')}), waiting {wait_time:.1f} seconds...")
                    time.sleep(wait_time)
                    continue # <<< Continue within loop

                if isinstance(task_status_result, list) and len(task_status_result) > 0:
                    print("Got valid output result, processing files...")
                    consecutive_empty_results = 0  # Reset counter on successful result
                    image_urls = []
                    frame_video_urls = []  # For extracting frames from videos
                    video_urls = []        # For ComfyUI VIDEO output (mp4 files)
                    latent_urls = []
                    text_urls = []
                    audio_urls = [] # <<< Add list for audio urls

                    for output in task_status_result: # <<< Indent loop correctly
                        if isinstance(output, dict):
                            file_url = output.get("fileUrl")
                            file_type = output.get("fileType")
                            if file_url and file_type:
                                file_type_lower = file_type.lower()
                                if file_type_lower in ["png", "jpg", "jpeg", "webp", "bmp", "gif"]:
                                    image_urls.append(file_url)
                                elif file_type_lower in ["mp4", "avi", "mov", "webm"]:
                                    # Add to both frame extraction and video output
                                    frame_video_urls.append(file_url)  # For frame extraction
                                    if file_type_lower == "mp4":  # Only mp4 for VIDEO output
                                        video_urls.append(file_url)
                                elif file_type_lower == "latent":
                                    latent_urls.append(file_url)
                                elif file_type_lower == "txt":
                                    text_urls.append(file_url)
                                # <<< Add common audio types
                                elif file_type_lower in ["wav", "mp3", "flac", "ogg"]: 
                                    audio_urls.append(file_url)

                    # Process Images -> Add to image_data_list
                    if image_urls:
                        print(f"Processing {len(image_urls)} images...")
                        # Download all images first
                        downloaded_images = []
                        for url in image_urls:
                            try:
                                img_tensor = self.download_image(url)
                                if img_tensor is not None:
                                    downloaded_images.append(img_tensor)
                                    print(f"Successfully downloaded image from {url} (Shape: {img_tensor.shape})")
                                # Remove the break to process all images
                                # print(f"Successfully processed first image from {url}. Skipping remaining images.")
                                # break # Process only the first image
                            except Exception as img_e:
                                print(f"Error downloading image {url}: {img_e}")
                        
                        # If images were downloaded, normalize channels and find max dimensions
                        if downloaded_images:
                            if len(downloaded_images) > 1:
                                print("Multiple images found. Normalizing channels and checking dimensions...")
                                
                                # Check channel counts and find max
                                max_channels = 0
                                max_h = 0
                                max_w = 0
                                for img in downloaded_images:
                                    # Shape is [1, H, W, C]
                                    max_h = max(max_h, img.shape[1])
                                    max_w = max(max_w, img.shape[2])
                                    max_channels = max(max_channels, img.shape[3])
                                
                                print(f"Max dimensions: Height={max_h}, Width={max_w}, Channels={max_channels}")

                                # Normalize all images to same channel count and dimensions
                                normalized_images = []
                                for i, img_tensor in enumerate(downloaded_images):
                                    _, h, w, c = img_tensor.shape
                                    current_img = img_tensor
                                    
                                    # Normalize channels first
                                    if c < max_channels:
                                        if c == 3 and max_channels == 4:
                                            # Add alpha channel (full opacity)
                                            alpha_channel = torch.ones(1, h, w, 1, dtype=current_img.dtype, device=current_img.device)
                                            current_img = torch.cat([current_img, alpha_channel], dim=3)
                                            print(f"  Added alpha channel to image {i+1} (RGB->RGBA)")
                                        else:
                                            # General case: pad with zeros
                                            padding_channels = max_channels - c
                                            padding = torch.zeros(1, h, w, padding_channels, dtype=current_img.dtype, device=current_img.device)
                                            current_img = torch.cat([current_img, padding], dim=3)
                                            print(f"  Padded channels for image {i+1} from {c} to {max_channels}")
                                    
                                    # Then normalize spatial dimensions
                                    if h < max_h or w < max_w:
                                        pad_h_total = max_h - h
                                        pad_w_total = max_w - w
                                        pad_top = pad_h_total // 2
                                        pad_bottom = pad_h_total - pad_top
                                        pad_left = pad_w_total // 2
                                        pad_right = pad_w_total - pad_left
                                        
                                        # Permute [1, H, W, C] -> [1, C, H, W] for F.pad
                                        img_permuted = current_img.permute(0, 3, 1, 2)
                                        # Pad spatial dimensions (pad is specified for last dimensions first: W, then H)
                                        # For RGBA images, pad with 0 for RGB channels and 1 for alpha channel
                                        if max_channels == 4:
                                            # Pad RGB channels with 0, alpha channel with 0 (transparent)
                                            padded_permuted = F.pad(img_permuted, (pad_left, pad_right, pad_top, pad_bottom), "constant", 0)
                                        else:
                                            padded_permuted = F.pad(img_permuted, (pad_left, pad_right, pad_top, pad_bottom), "constant", 0)
                                        
                                        # Permute back [1, C, H, W] -> [1, H, W, C]
                                        padded_img = padded_permuted.permute(0, 2, 3, 1)
                                        print(f"  Padded image {i+1} from {h}x{w} to {max_h}x{max_w}")
                                        normalized_images.append(padded_img)
                                    else:
                                        print(f"  Image {i+1} already has max spatial dimensions.")
                                        normalized_images.append(current_img)
                                
                                image_data_list = normalized_images
                            else:
                                # Only one image, no normalization needed, just use it
                                print("Only one image found, no normalization needed.")
                                image_data_list = downloaded_images
                        # else: image_data_list remains empty

                    # Process Videos (extract frames) -> Add to frame_data_list
                    # Only extract frames if there's a single video; skip for multiple videos to save time
                    skipped_frame_extraction = False  # Flag to track if we skipped frame extraction
                    if frame_video_urls:
                        if len(frame_video_urls) > 1:
                            print(f"Multiple videos detected ({len(frame_video_urls)}). Skipping frame extraction to save processing time.")
                            print("video_frames output will show a placeholder image.")
                            # Don't extract frames, will use placeholder later
                            skipped_frame_extraction = True
                        else:
                            # Single video - extract frames as before
                            print(f"Processing single video for frames...")
                            for url in frame_video_urls:
                                try:
                                    frame_tensors = self.download_video(url)
                                    if frame_tensors:
                                        frame_data_list.extend(frame_tensors) # <<< Add to frame_data_list
                                        print(f"Extracted {len(frame_tensors)} frames from video {url}")
                                except Exception as vid_e:
                                    print(f"Error processing video {url}: {vid_e}")

                    # Process Latents (load the first one found)
                    if latent_urls and latent_data is None:
                        print(f"Processing {len(latent_urls)} latent file(s)...")
                        for url in latent_urls:
                             try:
                                 loaded_latent = self.download_and_load_latent(url)
                                 if loaded_latent is not None:
                                     latent_data = loaded_latent
                                     print(f"Successfully loaded latent from {url}")
                                     break # Process only the first successful latent
                             except Exception as lat_e:
                                 print(f"Error processing latent {url}: {lat_e}")

                    # Process Text Files (read the first one found)
                    if text_urls and text_data is None:
                        print(f"Processing {len(text_urls)} text file(s)...")
                        for url in text_urls:
                             try:
                                 loaded_text = self.download_and_read_text(url)
                                 if loaded_text is not None:
                                     text_data = loaded_text
                                     print(f"Successfully read text from {url}")
                                     break # Process only the first successful text file
                             except Exception as txt_e:
                                 print(f"Error processing text file {url}: {txt_e}")

                    # <<< Process Audio Files (load the first one found)
                    if audio_urls and audio_data is None:
                        print(f"Processing {len(audio_urls)} audio file(s)...")
                        for url in audio_urls:
                             try:
                                 loaded_audio = self.download_and_process_audio(url)
                                 if loaded_audio is not None:
                                     audio_data = loaded_audio
                                     print(f"Successfully processed audio from {url}")
                                     break # Process only the first successful audio file
                             except Exception as aud_e:
                                 print(f"Error processing audio file {url}: {aud_e}")

                    # <<< Process Video Files (download up to 5 mp4 files for VIDEO outputs)
                    video_data_list = []  # Store up to 5 video objects
                    if video_urls and video_support_available:
                        num_videos = len(video_urls)
                        max_videos = 5
                        videos_to_process = min(num_videos, max_videos)
                        
                        print(f"Processing {videos_to_process} mp4 video file(s) for VIDEO output (total found: {num_videos})...")
                        
                        for i, url in enumerate(video_urls[:max_videos]):  # Take only first 5
                             try:
                                 video_path = self.download_video_for_output(url)
                                 if video_path is not None:
                                     video_obj = VideoFromFile(video_path)
                                     video_data_list.append(video_obj)
                                     print(f"Successfully created VIDEO output {i+1} from {url}")
                             except Exception as vid_e:
                                 print(f"Error processing video file {url}: {vid_e}")
                        
                        if num_videos > max_videos:
                            print(f"Note: {num_videos - max_videos} additional video(s) were ignored (max 5 videos supported)")
                    elif video_urls and not video_support_available:
                        print(f"Found {len(video_urls)} mp4 files but VIDEO support not available")

                    # Task processing complete, break the retry loop
                    break # <<< Break within loop

                elif isinstance(task_status_result, dict) and task_status_result.get("taskStatus") == "error": # <<< Use elif
                    print(f"Task failed with error: {task_status_result.get('error', 'Unknown error')}")
                    break # <<< Break within loop

                elif isinstance(task_status_result, list) and len(task_status_result) == 0:
                    # Handle empty result list - backup detection for completed task with no output
                    consecutive_empty_results += 1
                    print(f"Received empty result list (attempt {consecutive_empty_results}/{max_consecutive_empty}) - backup detection")
                    
                    if consecutive_empty_results >= max_consecutive_empty:
                        # Task appears to be completed but with no output (should be rare with improved status detection)
                        raise Exception("Task completed successfully but the workflow produced no output results. Possible reasons: 1) Workflow is configured to execute but has no output nodes; 2) Output nodes are disabled; 3) Workflow logic resulted in no final output")
                    
                    # Wait before retrying
                    wait_time = min(retry_interval * (1.5 ** attempt), max_retry_interval)
                    print(f"Waiting {wait_time:.1f} seconds before checking again...")
                    time.sleep(wait_time)

                else: # <<< Handle other cases or unexpected results
                    print(f"Unexpected task status or empty result, waiting...")
                    consecutive_empty_results = 0  # Reset counter for other types of results
                    time.sleep(min(retry_interval * (1.5 ** attempt), max_retry_interval))

            except Exception as e: # <<< Added except clause
                print(f"Error checking/processing task status (attempt {attempt + 1}): {e}")
                # Check if the result indicates an error, even if an exception occurred during processing
                if isinstance(task_status_result, dict) and task_status_result.get("taskStatus") == "error":
                     print("Stopping retries due to reported task error.")
                     break # <<< Break within loop
                # Simple exponential backoff for retries
                time.sleep(min(retry_interval * (1.5 ** attempt), max_retry_interval))

        # --- Final Output Generation ---

        # Placeholder for regular images
        if not image_data_list:
            print("No regular images generated, creating placeholder.")
            image_data_list.append(self.create_placeholder_image(text="No image output"))

        # Placeholder for video frames
        if not frame_data_list:
            # Check if we skipped frame extraction due to multiple videos
            if 'skipped_frame_extraction' in locals() and skipped_frame_extraction:
                print("Frame extraction was skipped for multiple videos, creating placeholder.")
                frame_data_list.append(self.create_placeholder_image(text="Multiple videos - frames not extracted", width=512, height=128))
            else:
                print("No video frames generated, creating placeholder.")
                frame_data_list.append(self.create_placeholder_image(text="No video frame output"))

        # Placeholder for latent
        if latent_data is None:
            print("No latent generated, creating placeholder.")
            latent_data = self.create_placeholder_latent()

        # Default for text
        if text_data is None:
             print("No text file processed, returning 'null' string.")
             text_data = "null"
             
        # <<< Placeholder for audio
        if audio_data is None:
            print("No audio generated, creating placeholder.")
            audio_data = self.create_placeholder_audio()

        # <<< Prepare 5 video outputs (video1-video5)
        # Ensure video_data_list exists
        if 'video_data_list' not in locals():
            video_data_list = []
        
        # Pad video_data_list to exactly 5 elements with placeholder videos
        print(f"Preparing video outputs: {len(video_data_list)} video(s) available")
        while len(video_data_list) < 5:
            # Create placeholder video for empty slots
            placeholder_video = self.create_placeholder_video()
            video_data_list.append(placeholder_video)
        
        # Take only first 5 (in case somehow more were added)
        video_data_list = video_data_list[:5]
        
        video1, video2, video3, video4, video5 = video_data_list
        
        # Print summary of video outputs
        real_video_count = sum(1 for v in [video1, video2, video3, video4, video5] if v is not None and not (hasattr(v, 'path') and 'placeholder' in v.path))
        if real_video_count > 0:
            print(f"Returning {real_video_count} real video output(s) and {5 - real_video_count} placeholder(s) across video1-video5")
        else:
            print("No real videos generated, all 5 video outputs will use placeholders.")

        # Batch images and frames separately
        final_image_batch = torch.cat(image_data_list, dim=0) if image_data_list else None
        final_frame_batch = torch.cat(frame_data_list, dim=0) if frame_data_list else None # <<< Batch frames

        # Ensure we return a tuple matching RETURN_TYPES
        # <<< Add audio_data to the return tuple
        # Debug: Check audio_data before returning (ComfyUI expects 3D: [batch, channels, samples])
        if audio_data is not None and "waveform" in audio_data:
            waveform = audio_data['waveform']
            print(f"DEBUG: Final audio waveform shape: {waveform.shape}")
            print(f"DEBUG: Final audio waveform ndim: {waveform.ndim}")
            print(f"DEBUG: Final audio waveform dtype: {waveform.dtype}")
            
            # Verify ComfyUI standard format
            if waveform.ndim == 3:
                print(f"DEBUG: ✓ Correct ComfyUI format [batch={waveform.shape[0]}, channels={waveform.shape[1]}, samples={waveform.shape[2]}]")
            else:
                print(f"DEBUG: ✗ Unexpected format - ComfyUI expects 3D [batch, channels, samples]")
        else:
            print(f"DEBUG: audio_data is None or missing waveform key")
        
        return (final_image_batch, final_frame_batch, latent_data, text_data, audio_data, video1, video2, video3, video4, video5) 

    def create_placeholder_image(self, text="No image/video output", width=256, height=64, with_alpha=False):
        """Creates a placeholder image tensor with text.
        
        Args:
            text: Text to display on the placeholder
            width: Image width
            height: Image height  
            with_alpha: If True, creates RGBA image with alpha channel, otherwise RGB
        """
        # Create image with or without alpha channel
        if with_alpha:
            img = Image.new('RGBA', (width, height), color=(50, 50, 50, 255)) # Dark gray background, fully opaque
            print("Creating RGBA placeholder image")
        else:
            img = Image.new('RGB', (width, height), color=(50, 50, 50)) # Dark gray background
            print("Creating RGB placeholder image")
            
        d = ImageDraw.Draw(img)
        try:
            # Attempt to load a simple default font (may vary by system)
            # A small default size to fit the image
            fontsize = 15
            # Try common system font names/paths
            font_paths = ["arial.ttf", "LiberationSans-Regular.ttf", "DejaVuSans.ttf"]
            font = None
            for fp in font_paths:
                try:
                    font = ImageFont.truetype(fp, fontsize)
                    break
                except IOError:
                    continue
            if font is None:
                 font = ImageFont.load_default() # Fallback to PIL default bitmap font
                 print("Warning: Could not load system font, using PIL default.")

            # Calculate text position for centering
            text_bbox = d.textbbox((0, 0), text, font=font)
            text_width = text_bbox[2] - text_bbox[0]
            text_height = text_bbox[3] - text_bbox[1]
            text_x = (width - text_width) / 2
            text_y = (height - text_height) / 2
            
            # Text color based on image mode
            if with_alpha:
                d.text((text_x, text_y), text, fill=(200, 200, 200, 255), font=font) # Light gray text, fully opaque
            else:
                d.text((text_x, text_y), text, fill=(200, 200, 200), font=font) # Light gray text
        except Exception as e:
            print(f"Error adding text to placeholder image: {e}. Returning image without text.")
        
        img_array = np.array(img).astype(np.float32) / 255.0
        img_tensor = torch.from_numpy(img_array)[None,] # Shape: [1, H, W, C] where C=3 or 4
        print(f"Placeholder image tensor shape: {img_tensor.shape}")
        return img_tensor

    def create_placeholder_latent(self, batch_size=1, channels=4, height=64, width=64):
        """Creates a placeholder latent tensor dictionary."""
        latent = torch.zeros([batch_size, channels, height, width])
        return {"samples": latent}
        
    # <<< Add placeholder audio function
    def create_placeholder_audio(self, sample_rate=44100, duration_sec=0.01):
        """Creates a placeholder silent audio dictionary following ComfyUI's standard format."""
        print(f"Creating silent placeholder audio: {duration_sec}s @ {sample_rate}Hz")
        num_samples = int(sample_rate * duration_sec)
        
        # Create stereo waveform with shape [channels, samples] first
        waveform = torch.zeros((2, num_samples), dtype=torch.float32)
        
        # Add batch dimension as per ComfyUI standard: [batch, channels, samples]
        # This matches ComfyUI's LoadAudio implementation: waveform.unsqueeze(0)
        waveform = waveform.unsqueeze(0)  # Now shape is [1, 2, num_samples]
        
        print(f"Placeholder audio tensor shape: {waveform.shape} [batch, channels, samples]")
        print(f"Placeholder audio tensor ndim: {waveform.ndim}, dtype: {waveform.dtype}")
        
        # Create audio dictionary following ComfyUI standard
        audio_dict = {
            "waveform": waveform,
            "sample_rate": sample_rate
        }
        
        print(f"Created ComfyUI-standard audio dictionary with keys: {list(audio_dict.keys())}")
        return audio_dict

    def create_placeholder_video(self):
        """Creates a placeholder video file and returns a VideoFromFile object.
        
        This creates a minimal 1-second black video at 1 fps to satisfy downstream video nodes.
        """
        import tempfile
        
        try:
            print("Creating placeholder video for empty video output...")
            
            # Create temporary file with .mp4 extension
            temp_video_fd, temp_video_path = tempfile.mkstemp(suffix='_placeholder.mp4', prefix='video_placeholder_')
            os.close(temp_video_fd)  # Close file descriptor, we'll write with cv2
            
            # Video properties
            width, height = 64, 64
            fps = 1
            duration_frames = 1  # Just 1 frame
            
            # Create video writer
            fourcc = cv2.VideoWriter_fourcc(*'mp4v')
            out = cv2.VideoWriter(temp_video_path, fourcc, fps, (width, height))
            
            if not out.isOpened():
                print("Warning: Failed to create VideoWriter, trying alternate codec...")
                fourcc = cv2.VideoWriter_fourcc(*'avc1')
                out = cv2.VideoWriter(temp_video_path, fourcc, fps, (width, height))
            
            if not out.isOpened():
                print("Error: Could not create placeholder video, returning None")
                os.remove(temp_video_path)
                return None
            
            # Write black frames
            black_frame = np.zeros((height, width, 3), dtype=np.uint8)
            for _ in range(duration_frames):
                out.write(black_frame)
            
            out.release()
            
            print(f"Created placeholder video at: {temp_video_path}")
            
            # Return VideoFromFile object
            return VideoFromFile(temp_video_path)
            
        except Exception as e:
            print(f"Error creating placeholder video: {e}")
            return None

    def download_image(self, image_url):
        """
        Download image from URL and convert to torch.Tensor format suitable for preview or save.
        Includes retry mechanism with maximum 5 retries.
        Preserves PNG alpha channel (mask information).
        Returns tensor [1, H, W, C] or None on failure, where C=3 for RGB or C=4 for RGBA.
        """
        max_retries = 5
        retry_delay = 1
        last_exception = None
        img_tensor = None # Define img_tensor outside try

        for attempt in range(max_retries):
            try:
                response = requests.get(image_url, timeout=30)
                print(f"Download image attempt {attempt + 1} ({image_url}): Status code: {response.status_code}")
                response.raise_for_status()

                content_type = response.headers.get('Content-Type', '').lower()
                # Consider validating content_type if needed

                # Open image and preserve alpha channel if present
                img = Image.open(BytesIO(response.content))
                original_mode = img.mode
                print(f"Original image mode: {original_mode}")
                
                # Preserve alpha channel for PNG images, convert others to RGB
                if original_mode in ['RGBA', 'LA'] or (original_mode == 'P' and 'transparency' in img.info):
                    # Convert to RGBA to preserve alpha/transparency information
                    img = img.convert("RGBA")
                    print("Preserving alpha channel (RGBA)")
                else:
                    # Convert to RGB for images without alpha channel
                    img = img.convert("RGB")
                    print("Converting to RGB (no alpha channel)")
                
                img_array = np.array(img).astype(np.float32) / 255.0
                img_tensor = torch.from_numpy(img_array)[None,] # Shape: [1, H, W, C] where C=3 or 4
                print(f"Final tensor shape: {img_tensor.shape}")
                return img_tensor # Return on success

            except (requests.exceptions.RequestException, IOError, Image.UnidentifiedImageError) as e: # <<< Correct except clause
                print(f"Download image attempt {attempt + 1} failed: {e}")
                last_exception = e
                if attempt < max_retries - 1:
                    print(f"Retrying in {retry_delay} seconds...")
                    time.sleep(retry_delay)
                    retry_delay *= 2
                # else: # <<< Implicitly handled by loop ending
                #     print(f"Failed to download image {image_url} after {max_retries} attempts.")
                #     # Keep img_tensor as None

        # If loop finishes without returning, it means all retries failed
        print(f"Failed to download image {image_url} after {max_retries} attempts.")
        return None


    def download_video(self, video_url):
        """
        Downloads a video, extracts all frames, converts them to tensors,
        deletes the video file, and returns a list of image tensors.
        Requires opencv-python (cv2).
        Returns list[torch.Tensor] or None on failure. Each tensor shape [1, H, W, C]. <<< Updated shape comment
        """
        max_retries = 5
        retry_delay = 1
        last_exception = None
        video_path = None
        output_dir = "temp" # Use a temp directory for downloaded videos

        # --- Ensure temp directory exists ---
        if not os.path.exists(output_dir): # <<< Correct indentation
            try:
                os.makedirs(output_dir)
                print(f"Created temporary directory: {output_dir}")
            except OSError as e:
                print(f"Error creating temporary directory {output_dir}: {e}")
                return None # Cannot proceed without temp dir

        # --- Download the video file ---
        for attempt in range(max_retries):
            video_path = None # Reset path for each attempt
            try:
                # Generate a unique temporary filename
                try:
                    safe_filename = f"temp_video_{os.path.basename(video_url)}_{str(int(time.time()*1000))}.tmp"
                    safe_filename = "".join(c if c.isalnum() or c in ['.', '_', '-'] else '_' for c in safe_filename)[:150] # Basic sanitization and length limit
                    video_path = os.path.join(output_dir, safe_filename)
                except Exception as path_e:
                    print(f"Error creating temporary video path: {path_e}")
                    # Fallback filename
                    video_path = os.path.join(output_dir, f"temp_video_{str(int(time.time()*1000))}.tmp")

                print(f"Attempt {attempt + 1}/{max_retries} to download video to temp path: {video_path}")
                response = requests.get(video_url, stream=True, timeout=60)
                response.raise_for_status()

                downloaded_size = 0
                with open(video_path, "wb") as f:
                    for chunk in response.iter_content(chunk_size=65536):
                        if chunk:
                            f.write(chunk)
                            downloaded_size += len(chunk)

                if downloaded_size > 0:
                    print(f"Temporary video downloaded successfully: {video_path}")
                    break # Exit retry loop on successful download
                else:
                    print(f"Warning: Downloaded video file is empty: {video_path}")
                    if os.path.exists(video_path):
                        try: os.remove(video_path)
                        except OSError: pass
                    last_exception = IOError("Downloaded video file is empty.")
                    # Continue to retry

            except (requests.exceptions.RequestException, IOError) as e: # <<< Correct except clause
                 print(f"Download video attempt {attempt + 1} failed: {e}")
                 last_exception = e
                 if video_path and os.path.exists(video_path):
                     try: os.remove(video_path)
                     except OSError: pass # Ignore error removing partial file
                 # Continue to retry unless it's the last attempt

            if attempt < max_retries - 1:
                print(f"Retrying download in {retry_delay} seconds...")
                time.sleep(retry_delay)
                retry_delay *= 2
            # else: # Implicitly handled by loop ending
            #     print(f"Failed to download video {video_url} after {max_retries} attempts.")
            #     # video_path will likely be None or point to a non-existent/empty file

        # Check if download succeeded (video_path exists and is not empty)
        if not video_path or not os.path.exists(video_path) or os.path.getsize(video_path) == 0:
             print(f"Failed to download video {video_url} successfully after {max_retries} attempts.")
             # Clean up potentially empty file if it exists
             if video_path and os.path.exists(video_path):
                 try: os.remove(video_path)
                 except OSError: pass
             return None

        # --- Extract frames if download was successful ---
        frame_tensors = []
        cap = None
        try:
            print(f"Extracting frames from {video_path}...")
            cap = cv2.VideoCapture(video_path)
            if not cap.isOpened():
                raise IOError(f"Cannot open video file: {video_path}")

            frame_count = 0
            while True:
                ret, frame = cap.read()
                if not ret:
                    break # End of video

                # Convert frame (BGR) to RGB, then to Tensor [1, H, W, C] (float32, 0-1)
                frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                # Reuse PIL conversion for consistency? Or keep cv2->numpy path
                img_array = frame_rgb.astype(np.float32) / 255.0 # Direct conversion
                # img = Image.fromarray(frame_rgb)
                # img_array = np.array(img).astype(np.float32) / 255.0
                img_tensor = torch.from_numpy(img_array)[None,] # <<< Added batch dimension
                frame_tensors.append(img_tensor)
                frame_count += 1
                # Optional: Add progress logging for long videos
                # if frame_count % 100 == 0: print(f"  Extracted {frame_count} frames...")

            print(f"Finished extracting {frame_count} frames.")
        except Exception as e:
            print(f"Error extracting frames from video {video_path}: {e}")
            # Return None or potentially partially extracted frames? Let's return None for consistency.
            frame_tensors = None # Indicate failure
        finally:
            # --- Cleanup ---
            if cap:
                cap.release()
            # Delete the temporary video file regardless of extraction success/failure
            if video_path and os.path.exists(video_path):
                try:
                    os.remove(video_path)
                    print(f"Deleted temporary video file: {video_path}")
                except OSError as e:
                    print(f"Error deleting temporary video file {video_path}: {e}")

        return frame_tensors

    def download_video_for_output(self, video_url):
        """
        Downloads a video file for ComfyUI VIDEO output and returns the file path.
        Unlike download_video(), this method does NOT delete the file as VideoFromFile needs the path.
        Returns the path to the downloaded video file or None on failure.
        """
        max_retries = 5
        retry_delay = 1
        last_exception = None
        video_path = None
        output_dir = "temp"

        # --- Ensure temp directory exists ---
        if not os.path.exists(output_dir):
            try:
                os.makedirs(output_dir)
                print(f"Created temporary directory: {output_dir}")
            except OSError as e:
                print(f"Error creating temporary directory {output_dir}: {e}")
                return None

        # --- Download the video file ---
        for attempt in range(max_retries):
            video_path = None
            try:
                # Generate a unique filename for VIDEO output (persistent)
                try:
                    safe_filename = f"video_output_{os.path.basename(video_url)}_{str(int(time.time()*1000))}.mp4"
                    safe_filename = "".join(c if c.isalnum() or c in ['.', '_', '-'] else '_' for c in safe_filename)[:150]
                    video_path = os.path.join(output_dir, safe_filename)
                except Exception as path_e:
                    print(f"Error creating video output path: {path_e}")
                    video_path = os.path.join(output_dir, f"video_output_{str(int(time.time()*1000))}.mp4")

                print(f"Attempt {attempt + 1}/{max_retries} to download video for OUTPUT to: {video_path}")
                response = requests.get(video_url, stream=True, timeout=60)
                response.raise_for_status()

                downloaded_size = 0
                with open(video_path, "wb") as f:
                    for chunk in response.iter_content(chunk_size=65536):
                        if chunk:
                            f.write(chunk)
                            downloaded_size += len(chunk)

                if downloaded_size > 0:
                    print(f"Video downloaded successfully for OUTPUT: {video_path} ({downloaded_size} bytes)")
                    return video_path  # Return path for VideoFromFile
                else:
                    print(f"Warning: Downloaded video file is empty: {video_path}")
                    if os.path.exists(video_path):
                        try: os.remove(video_path)
                        except OSError: pass
                    last_exception = IOError("Downloaded video file is empty.")

            except (requests.exceptions.RequestException, IOError) as e:
                 print(f"Download video for output attempt {attempt + 1} failed: {e}")
                 last_exception = e
                 if video_path and os.path.exists(video_path):
                     try: os.remove(video_path)
                     except OSError: pass

            if attempt < max_retries - 1:
                print(f"Retrying download in {retry_delay} seconds...")
                time.sleep(retry_delay)
                retry_delay *= 2

        # All attempts failed
        print(f"Failed to download video {video_url} for OUTPUT after {max_retries} attempts.")
        return None

    def download_and_load_latent(self, latent_url):
        """
        Downloads a .latent file, loads it using safetensors, applies multiplier,
        cleans up the temp file, and returns the latent dictionary.
        Returns dict { "samples": tensor } or None on failure.
        """
        max_retries = 5
        retry_delay = 1
        last_exception = None
        latent_path = None
        output_dir = "temp" # Use temp directory

        # Ensure temp directory exists
        if not os.path.exists(output_dir):
            try:
                os.makedirs(output_dir)
            except OSError as e:
                print(f"Error creating temporary directory {output_dir}: {e}")
                return None

        # --- Download the latent file ---
        for attempt in range(max_retries):
            latent_path = None # Reset path for each attempt
            try:
                # Generate a unique temporary filename
                try:
                    safe_filename = f"temp_latent_{os.path.basename(latent_url)}_{str(int(time.time()*1000))}.latent"
                    safe_filename = "".join(c if c.isalnum() or c in ['.', '_', '-'] else '_' for c in safe_filename)[:150]
                    latent_path = os.path.join(output_dir, safe_filename)
                except Exception as path_e:
                    print(f"Error creating temporary latent path: {path_e}")
                    latent_path = os.path.join(output_dir, f"temp_latent_{str(int(time.time()*1000))}.latent")

                print(f"Attempt {attempt + 1}/{max_retries} to download latent to temp path: {latent_path}")
                response = requests.get(latent_url, stream=True, timeout=30)
                response.raise_for_status()

                downloaded_size = 0
                with open(latent_path, "wb") as f:
                    for chunk in response.iter_content(chunk_size=65536):
                        if chunk: # <<< Correct indent
                            f.write(chunk)
                            downloaded_size += len(chunk) # <<< Correct indent

                if downloaded_size > 0:
                    print(f"Temporary latent downloaded successfully: {latent_path}")
                    break # Exit retry loop on successful download
                else:
                    print(f"Warning: Downloaded latent file is empty: {latent_path}")
                    if os.path.exists(latent_path):
                        try: os.remove(latent_path)
                        except OSError: pass
                    last_exception = IOError("Downloaded latent file is empty.")
                    # Continue retry loop

            except (requests.exceptions.RequestException, IOError) as e:
                 print(f"Download latent attempt {attempt + 1} failed: {e}")
                 last_exception = e
                 if latent_path and os.path.exists(latent_path):
                     try: os.remove(latent_path)
                     except OSError: pass
                 # Continue retry loop

            if attempt < max_retries - 1:
                print(f"Retrying download in {retry_delay} seconds...")
                time.sleep(retry_delay)
                retry_delay *= 2
            # else: # Implicitly handled by loop ending
            #     print(f"Failed to download latent {latent_url} after {max_retries} attempts.")

        # Check if download succeeded
        if not latent_path or not os.path.exists(latent_path) or os.path.getsize(latent_path) == 0:
             print(f"Failed to download latent {latent_url} successfully after {max_retries} attempts.")
             if latent_path and os.path.exists(latent_path):
                 try: os.remove(latent_path)
                 except OSError: pass
             return None

        # --- Load the latent file ---
        loaded_latent_dict = None
        try:
            print(f"Loading latent from {latent_path}...")
            # Use safetensors.torch.load_file
            latent_content = safetensors.torch.load_file(latent_path, device="cpu")

            if "latent_tensor" not in latent_content:
                 raise ValueError("'latent_tensor' key not found in the loaded latent file.")

            # Apply multiplier based on LoadLatent logic
            multiplier = 1.0
            if "latent_format_version_0" not in latent_content:
                multiplier = 1.0 / 0.18215
                print(f"Applying multiplier {multiplier:.5f} (old latent format detected)")

            samples_tensor = latent_content["latent_tensor"].float() * multiplier
            loaded_latent_dict = {"samples": samples_tensor}
            print("Latent loaded successfully.")

        except Exception as e:
            print(f"Error loading latent file {latent_path}: {e}")
            # Ensure loaded_latent_dict remains None on error
            loaded_latent_dict = None
        finally:
            # --- Cleanup ---
            if latent_path and os.path.exists(latent_path):
                try:
                    os.remove(latent_path)
                    print(f"Deleted temporary latent file: {latent_path}")
                except OSError as e:
                    print(f"Error deleting temporary latent file {latent_path}: {e}")

        return loaded_latent_dict

    def download_and_read_text(self, text_url):
        """
        Downloads a .txt file, reads its content as UTF-8,
        cleans up the temp file, and returns the text content.
        Returns str or None on failure.
        """
        max_retries = 5
        retry_delay = 1
        last_exception = None
        text_path = None
        output_dir = "temp"

        if not os.path.exists(output_dir):
            try: os.makedirs(output_dir)
            except OSError as e: print(f"Error creating temp dir {output_dir}: {e}"); return None

        # --- Download the text file ---
        for attempt in range(max_retries):
            text_path = None
            try:
                try:
                    safe_filename = f"temp_text_{os.path.basename(text_url)}_{str(int(time.time()*1000))}.txt"
                    safe_filename = "".join(c if c.isalnum() or c in ['.', '_', '-'] else '_' for c in safe_filename)[:150]
                    text_path = os.path.join(output_dir, safe_filename)
                except Exception as path_e:
                    print(f"Error creating temporary text path: {path_e}")
                    text_path = os.path.join(output_dir, f"temp_text_{str(int(time.time()*1000))}.txt")

                print(f"Attempt {attempt + 1}/{max_retries} to download text to temp path: {text_path}")
                response = requests.get(text_url, stream=True, timeout=20) # Shorter timeout for text
                response.raise_for_status()

                downloaded_size = 0
                with open(text_path, "wb") as f: # Write in binary first
                    for chunk in response.iter_content(chunk_size=4096):
                        if chunk: f.write(chunk); downloaded_size += len(chunk)

                if downloaded_size > 0:
                    print(f"Temporary text file downloaded: {text_path}")
                    break # Success
                else:
                    if os.path.exists(text_path):
                         try: os.remove(text_path)
                         except OSError: pass
                    last_exception = IOError("Downloaded text file is empty.")
                    # Continue retries

            except (requests.exceptions.RequestException, IOError) as e:
                 print(f"Download text attempt {attempt + 1} failed: {e}")
                 last_exception = e
                 if text_path and os.path.exists(text_path):
                     try: os.remove(text_path)
                     except OSError: pass
                 # Continue retries

            if attempt < max_retries - 1:
                print(f"Retrying download in {retry_delay} seconds...")
                time.sleep(retry_delay); retry_delay *= 2
            # else: # Implicitly handled by loop ending
            #     print(f"Failed to download text {text_url} after {max_retries} attempts.")

        # Check download success
        if not text_path or not os.path.exists(text_path) or os.path.getsize(text_path) == 0:
             print(f"Failed to download text {text_url} successfully after {max_retries} attempts.")
             if text_path and os.path.exists(text_path):
                 try: os.remove(text_path)
                 except OSError: pass
             return None

        # --- Read the text file ---
        read_content = None
        try:
            print(f"Reading text from {text_path}...")
            # Read with UTF-8 encoding, handle potential errors
            with open(text_path, "r", encoding="utf-8", errors="replace") as f:
                read_content = f.read()
            print("Text read successfully.")
        except Exception as e:
            print(f"Error reading text file {text_path}: {e}")
            read_content = None
        finally:
            # --- Cleanup ---
            if text_path and os.path.exists(text_path):
                try:
                    os.remove(text_path)
                    print(f"Deleted temporary text file: {text_path}")
                except OSError as e:
                    print(f"Error deleting temporary text file {text_path}: {e}")

        return read_content

    # <<< Add audio download and processing function
    def download_and_process_audio(self, audio_url):
        """
        Downloads an audio file, processes it using torchaudio,
        cleans up the temp file, and returns the audio dictionary.
        Returns dict { "waveform": tensor [Channels, Samples], "sample_rate": int } or None on failure.
        """
        max_retries = 5
        retry_delay = 1
        last_exception = None
        audio_path = None
        output_dir = "temp"

        if not os.path.exists(output_dir):
            try: os.makedirs(output_dir)
            except OSError as e: print(f"Error creating temp dir {output_dir}: {e}"); return None

        # --- Download the audio file ---
        for attempt in range(max_retries):
            audio_path = None
            try:
                # Generate temp filename based on URL extension if possible
                try:
                    basename = os.path.basename(audio_url)
                    _, ext = os.path.splitext(basename)
                    if not ext: ext = ".audio" # Default if no extension
                    safe_filename = f"temp_audio_{str(int(time.time()*1000))}{ext}"
                    safe_filename = "".join(c if c.isalnum() or c in ['.', '_', '-'] else '_' for c in safe_filename)[:150]
                    audio_path = os.path.join(output_dir, safe_filename)
                except Exception as path_e:
                    print(f"Error creating temporary audio path: {path_e}")
                    audio_path = os.path.join(output_dir, f"temp_audio_{str(int(time.time()*1000))}.tmp")

                print(f"Attempt {attempt + 1}/{max_retries} to download audio to temp path: {audio_path}")
                response = requests.get(audio_url, stream=True, timeout=60) # Longer timeout for audio/video
                response.raise_for_status()

                downloaded_size = 0
                with open(audio_path, "wb") as f:
                    for chunk in response.iter_content(chunk_size=65536):
                        if chunk: f.write(chunk); downloaded_size += len(chunk)

                if downloaded_size > 0:
                    print(f"Temporary audio file downloaded: {audio_path} ({downloaded_size} bytes)")
                    break # Success
                else:
                    if os.path.exists(audio_path):
                        try: os.remove(audio_path)
                        except OSError: pass
                    last_exception = IOError("Downloaded audio file is empty.")
                    # Continue retries

            except (requests.exceptions.RequestException, IOError) as e:
                 print(f"Download audio attempt {attempt + 1} failed: {e}")
                 last_exception = e
                 if audio_path and os.path.exists(audio_path):
                     try: os.remove(audio_path)
                     except OSError: pass
                 # Continue retries

            if attempt < max_retries - 1:
                print(f"Retrying download in {retry_delay} seconds...")
                time.sleep(retry_delay); retry_delay *= 2
            # else:
            #     print(f"Failed to download audio {audio_url} after {max_retries} attempts.")

        # Check download success
        if not audio_path or not os.path.exists(audio_path) or os.path.getsize(audio_path) == 0:
             print(f"Failed to download audio {audio_url} successfully after {max_retries} attempts.")
             if audio_path and os.path.exists(audio_path):
                 try: os.remove(audio_path)
                 except OSError: pass
             return None

        # --- Process the audio file ---
        processed_audio = None
        try:
            print(f"Processing audio from {audio_path} using torchaudio...")
            # Use torchaudio.load to get waveform and sample rate
            waveform, sample_rate = torchaudio.load(audio_path)
            
            # Ensure waveform is float32, which is common for ComfyUI audio nodes
            if waveform.dtype != torch.float32:
                print(f"Converting waveform from {waveform.dtype} to float32.")
                waveform = waveform.to(torch.float32)
                
            # <<< Ensure the tensor is contiguous <<<
            if not waveform.is_contiguous():
                print("Audio waveform is not contiguous. Making it contiguous.")
                waveform = waveform.contiguous()

            # torchaudio.load returns [channels, samples]
            # Follow ComfyUI standard format by adding batch dimension
            
            # Ensure mono audio becomes stereo for better compatibility
            if waveform.shape[0] == 1:
                # Convert mono to stereo by duplicating the channel
                waveform = waveform.repeat(2, 1)
                print(f"Converted mono audio to stereo: {waveform.shape}")
            
            # Add batch dimension as per ComfyUI standard: [batch, channels, samples]
            # This matches ComfyUI's LoadAudio implementation: waveform.unsqueeze(0)
            waveform = waveform.unsqueeze(0)
            
            print(f"Audio loaded successfully: Shape={waveform.shape} [batch, channels, samples], Sample Rate={sample_rate} Hz, dtype={waveform.dtype}")
            
            # Create audio dictionary following ComfyUI standard
            processed_audio = {
                "waveform": waveform,
                "sample_rate": sample_rate
            }
            print(f"Created ComfyUI-standard processed audio dictionary with keys: {list(processed_audio.keys())}")

        except Exception as e:
            print(f"Error processing audio file {audio_path} with torchaudio: {e}")
            processed_audio = None # Ensure it's None on error
        finally:
            # --- Cleanup ---
            if audio_path and os.path.exists(audio_path):
                try:
                    os.remove(audio_path)
                    print(f"Deleted temporary audio file: {audio_path}")
                except OSError as e:
                    print(f"Error deleting temporary audio file {audio_path}: {e}")

        return processed_audio


    def check_account_status(self, api_key, base_url):
        """
        Query account status and check if new tasks can be submitted. Includes retry mechanism.
        """
        if not api_key or not base_url:
            raise ValueError("API Key and Base URL are required for checking account status.")

        url = f"{base_url}/uc/openapi/accountStatus"
        headers = {
            "User-Agent": "ComfyUI-RH-APICall-Node/1.0",
            "Content-Type": "application/json",
        }
        data = {"apikey": api_key}

        max_retries = 5
        retry_delay = 1
        last_exception = None

        for attempt in range(max_retries):
            response = None
            try: # <<< Added try block
                print(f"Attempt {attempt + 1}/{max_retries} to check account status...")
                response = requests.post(url, json=data, headers=headers, timeout=15)
                response.raise_for_status()

                result = response.json()

                if result.get("code") != 0: # <<< Correct indent
                    api_msg = result.get('msg', 'Unknown API error')
                    print(f"API error on attempt {attempt + 1}: {api_msg}")
                    raise Exception(f"API error getting account status: {api_msg}")

                account_data = result.get("data")
                if not account_data or "currentTaskCounts" not in account_data:
                    raise ValueError("Invalid response structure for account status.")

                try: # <<< Correct indent (inner try for int conversion)
                    current_task_counts = int(account_data["currentTaskCounts"])
                    account_data["currentTaskCounts"] = current_task_counts
                    print("Account status check successful.")
                    return account_data # Success
                except (ValueError, TypeError) as e: # <<< Correct indent
                    raise ValueError(f"Invalid value for currentTaskCounts: {account_data.get('currentTaskCounts')}. Error: {e}")

            except (requests.exceptions.RequestException, json.JSONDecodeError, ValueError, Exception) as e: # <<< Correct indent
                print(f"Error on attempt {attempt + 1}/{max_retries}: {e}")
                last_exception = e
                if isinstance(e, json.JSONDecodeError) and response is not None:
                     print(f"Raw response text on JSON decode error: {response.text}")

                if attempt < max_retries - 1:
                    print(f"Retrying in {retry_delay} seconds...")
                    time.sleep(retry_delay)
                    retry_delay *= 2
                else:
                    print("Max retries reached for checking account status.")
                    raise Exception(f"Failed to check account status after {max_retries} attempts. Last error: {last_exception}") from last_exception

        # This should ideally not be reached if the loop logic is correct
        raise Exception(f"Failed to check account status after {max_retries} attempts (unexpected loop end). Last error: {last_exception}")


    def create_task(self, apiConfig, nodeInfoList, base_url, use_rtx4090_48g):
        """
        Create task with retry mechanism, maximum 5 retries
        """
        safe_base_url = apiConfig.get('base_url')
        # Use the updated key name here
        safe_workflow_id = apiConfig.get("workflowId_webappId") 
        safe_api_key = apiConfig.get("apiKey")

        if not safe_base_url or not safe_workflow_id or not safe_api_key:
             # Update the error message to reflect the new key
             raise ValueError("Missing required apiConfig fields: 'base_url', 'workflowId_webappId', 'apiKey'")

        url = f"{safe_base_url}/task/openapi/create"
        headers = {
            "Content-Type": "application/json",
            "User-Agent": "ComfyUI-RH-APICall-Node/1.0",
        }
        # Also update the key used in the API payload
        data = {
            "workflowId": safe_workflow_id, 
            "apiKey": safe_api_key,
            "nodeInfoList": nodeInfoList,
        }
        
        # Add instanceType if RTX 4090 48G is requested
        if use_rtx4090_48g:
            data["instanceType"] = "plus"

        max_retries = 5
        retry_delay = 1
        last_exception = None

        for attempt in range(max_retries):
            response = None
            current_last_exception = None
            success = False # Flag to indicate success within try block
            try:
                print(f"Create task attempt {attempt + 1}/{max_retries}...")
                response = requests.post(url, json=data, headers=headers, timeout=30)
                print(f"Create task attempt {attempt + 1}: Status code {response.status_code}")
                response.raise_for_status()

                result = response.json()

                if result.get("code") == 0:
                    if "data" in result and "taskId" in result["data"] and "netWssUrl" in result["data"]:
                         print("Task created successfully.")
                         success = True # Mark as success
                         return result # Return successful result
                    else:
                         print(f"API success code 0, but response structure invalid: {result}")
                         current_last_exception = ValueError(f"API success code 0, but response structure invalid.")
                else:
                    api_msg = result.get('msg', 'Unknown API error')
                    print(f"API error creating task (code {result.get('code')}): {api_msg}")
                    current_last_exception = Exception(f"API error (code {result.get('code')}): {api_msg}")

            except requests.exceptions.Timeout as e:
                 print(f"Create task attempt {attempt + 1} timed out.")
                 current_last_exception = e
            except requests.exceptions.RequestException as e:
                print(f"Create task attempt {attempt + 1} network error: {e}")
                current_last_exception = e
            except json.JSONDecodeError as e:
                 print(f"Create task attempt {attempt + 1} failed to decode JSON response.")
                 if response is not None: print(f"Raw response text: {response.text}")
                 current_last_exception = e
            except Exception as e:
                 print(f"Create task attempt {attempt + 1} unexpected error: {e}")
                 current_last_exception = e

            # If successful, we already returned. If not successful, process the error.
            if not success:
                 last_exception = current_last_exception # Store the most recent error
                 if attempt < max_retries - 1:
                     print(f"Retrying task creation in {retry_delay} seconds...")
                     time.sleep(retry_delay)
                     retry_delay *= 2
                 else: # Max retries reached
                     error_message = f"Failed to create task after {max_retries} attempts."
                     if last_exception:
                          error_message += f" Last error: {last_exception}"
                     print(error_message)
                     raise Exception(error_message) from last_exception

        # Should not be reachable if logic is correct
        raise Exception("Task creation failed unexpectedly after retry loop.")

    # <<< Add new function for creating AI App tasks >>>
    def create_ai_app_task(self, apiConfig, nodeInfoList, webappId, use_rtx4090_48g):
        """
        Create AI app task (using /task/openapi/ai-app/run) with retry mechanism.
        """
        safe_base_url = apiConfig.get('base_url')
        safe_api_key = apiConfig.get("apiKey")

        if not safe_base_url or not safe_api_key:
             raise ValueError("Missing required apiConfig fields: 'base_url', 'apiKey'")

        # <<< Use the AI App endpoint >>>
        url = f"{safe_base_url}/task/openapi/ai-app/run"
        headers = {
            "Content-Type": "application/json",
            "User-Agent": "ComfyUI-RH-APICall-Node/1.0",
            # Host header is typically handled by requests library
        }
        # <<< Construct payload for AI App task, converting webappId to int >>>
        try:
            webappId_int = int(webappId)
        except ValueError:
            # Handle error if the ID from config cannot be converted to int
            raise ValueError(f"Invalid webappId provided: '{webappId}'. It must be convertible to an integer.")

        data = {
            "webappId": webappId_int,
            "apiKey": safe_api_key,
            "nodeInfoList": nodeInfoList,
        }
        
        # Add instanceType if RTX 4090 48G is requested
        if use_rtx4090_48g:
            data["instanceType"] = "plus"

        max_retries = 5
        retry_delay = 1
        last_exception = None

        for attempt in range(max_retries):
            response = None
            current_last_exception = None
            success = False # Flag to indicate success within try block
            try:
                print(f"Create AI App task attempt {attempt + 1}/{max_retries}...")
                response = requests.post(url, json=data, headers=headers, timeout=30)
                print(f"Create AI App task attempt {attempt + 1}: Status code {response.status_code}")
                response.raise_for_status()

                result = response.json()

                # Response structure seems identical to standard task, check code and data fields
                if result.get("code") == 0:
                    if "data" in result and "taskId" in result["data"]: # Don't strictly require netWssUrl here
                         print("AI App Task created/queued successfully.")
                         success = True # Mark as success
                         return result # Return successful result
                    else:
                         print(f"AI App Task API success code 0, but response structure invalid: {result}")
                         current_last_exception = ValueError(f"API success code 0, but response structure invalid.")
                else:
                    api_msg = result.get('msg', 'Unknown API error')
                    print(f"API error creating AI App task (code {result.get('code')}): {api_msg}")
                    current_last_exception = Exception(f"API error (code {result.get('code')}): {api_msg}")

            except requests.exceptions.Timeout as e:
                 print(f"Create AI App task attempt {attempt + 1} timed out.")
                 current_last_exception = e
            except requests.exceptions.RequestException as e:
                print(f"Create AI App task attempt {attempt + 1} network error: {e}")
                current_last_exception = e
            except json.JSONDecodeError as e:
                 print(f"Create AI App task attempt {attempt + 1} failed to decode JSON response.")
                 if response is not None: print(f"Raw response text: {response.text}")
                 current_last_exception = e
            except Exception as e:
                 print(f"Create AI App task attempt {attempt + 1} unexpected error: {e}")
                 current_last_exception = e

            # If successful, we already returned. If not successful, process the error.
            if not success:
                 last_exception = current_last_exception # Store the most recent error
                 if attempt < max_retries - 1:
                     print(f"Retrying AI App task creation in {retry_delay} seconds...")
                     time.sleep(retry_delay)
                     retry_delay *= 2
                 else: # Max retries reached
                     error_message = f"Failed to create AI App task after {max_retries} attempts."
                     if last_exception:
                          error_message += f" Last error: {last_exception}"
                     print(error_message)
                     raise Exception(error_message) from last_exception

        # Should not be reachable if logic is correct
        raise Exception("AI App Task creation failed unexpectedly after retry loop.")


    def check_task_status(self, task_id, api_key, base_url):
        """
        Query task status. Returns a dictionary representing status or list of outputs on success.
        """
        if not task_id or not api_key or not base_url:
             raise ValueError("Task ID, API Key, and Base URL are required for checking task status.")
        url = f"{base_url}/task/openapi/outputs"
        headers = {
            "User-Agent": "ComfyUI-RH-APICall-Node/1.0",
            "Content-Type": "application/json",
        }
        data = { "taskId": task_id, "apiKey": api_key }

        response = None # Define response outside try
        result = None # Define result outside try
        
        # <<< Add retry loop for the network request itself <<<
        max_retries = 5
        retry_delay = 1
        last_exception = None

        for attempt in range(max_retries):
            try: # <<< Outer try block for requests/JSON processing
                print(f"Check status attempt {attempt + 1}/{max_retries} (TaskID: {task_id})...")
                response = requests.post(url, json=data, headers=headers, timeout=20)
                print(f"Check status ({task_id}): Response Status Code: {response.status_code}")

                # Process the response (JSON decoding, status checks) only if request succeeded
                try: # <<< Inner try block for JSON decoding
                    result = response.json()
                    print(f"Check status ({task_id}): Response JSON: {json.dumps(result, indent=2, ensure_ascii=False)}")
                except json.JSONDecodeError: # <<< Correct indent
                    print(f"Check status ({task_id}): Failed to decode JSON. Response Text: {response.text}")
                    error_msg = f"HTTP Error {response.status_code} and Invalid JSON" if response.status_code != 200 else "Invalid JSON response"
                    # Consider this a failure for retry purposes if status code indicates error
                    if response.status_code != 200:
                         raise requests.exceptions.RequestException(f"HTTP Error {response.status_code} with Invalid JSON")
                    else: # If status 200 but bad JSON, treat as terminal error for this check
                         return {"taskStatus": "error", "error": error_msg}

                # Process the decoded JSON result
                api_code = result.get("code") # <<< Correct indent
                api_msg = result.get("msg", "") # <<< Correct indent
                api_data = result.get("data") # <<< Correct indent

                # Handle Non-200 status codes AFTER potential JSON decoding
                if response.status_code != 200: # <<< Correct indent
                    error_detail = api_msg if api_msg else f"HTTP Error {response.status_code}"
                    print(f"Warning: Non-200 status code ({response.status_code}). API Message: {api_msg}")
                    # Raise exception to trigger retry for server-side issues (e.g., 5xx)
                    if 500 <= response.status_code < 600:
                         raise requests.exceptions.RequestException(f"Server Error {response.status_code}: {error_detail}")
                    else: # Treat other non-200 codes (like 4xx) as terminal for this check
                         return {"taskStatus": "error", "error": error_detail} # <<< Correct indent

                # --- If we got here, the request was successful (status 200, valid JSON) --- 
                # Now interpret the API result
                # 1. Check for successful completion (code 0, list data)
                if api_code == 0 and isinstance(api_data, list) and api_data:
                    return api_data # SUCCESS, return output data

                # 2. Check for explicit QUEUED message
                elif api_msg == "APIKEY_TASK_IS_QUEUED":
                    print(f"Check status ({task_id}): Task QUEUED.")
                    return {"taskStatus": "QUEUED"}

                # 3. Check for explicit RUNNING message
                elif api_msg == "APIKEY_TASK_IS_RUNNING":
                    possible_wss_url = None
                    if isinstance(api_data, dict): # Check if api_data is a dict
                        possible_wss_url = api_data.get("netWssUrl")
                    # No need to check result["data"] separately here, as api_data holds it

                    if possible_wss_url:
                        print(f"Check status ({task_id}): Task RUNNING, found netWssUrl.")
                        return {"taskStatus": "RUNNING", "netWssUrl": possible_wss_url}
                    else:
                        print(f"Check status ({task_id}): Task RUNNING, but netWssUrl not found in response data.")
                        return {"taskStatus": "RUNNING"} # Return RUNNING status without URL

                # 4. Check for API-reported errors (non-zero code, excluding specific handled messages)
                elif api_code != 0:
                    print(f"API Error checking status (code {api_code}): {api_msg}")
                    return {"taskStatus": "error", "error": api_msg}

                # 5. Check for code 0 with empty data list - this indicates task completed with no output
                elif api_code == 0 and isinstance(api_data, list) and not api_data:
                    print(f"Check status ({task_id}): Task completed successfully but produced no output (code 0, empty data list).")
                    return {"taskStatus": "completed_no_output"}
                
                # 6. Check for code 0 but no data (null) - this might indicate still running/initializing  
                elif api_code == 0 and api_data is None:
                    print(f"Check status ({task_id}): Task RUNNING (code 0, no data yet).")
                    return {"taskStatus": "RUNNING"}

                # 7. Fallback for unknown successful response structure
                else:
                    print(f"Unknown task status response structure: {result}")
                    return {"taskStatus": "unknown", "details": result}

            except requests.exceptions.Timeout as e: # <<< Correct except clause indent
                print(f"Network timeout on attempt {attempt + 1}/{max_retries} for task {task_id}")
                last_exception = e
                # Continue to retry loop
            except requests.exceptions.RequestException as e: # <<< Correct except clause indent
                print(f"Network error on attempt {attempt + 1}/{max_retries}: {e}")
                last_exception = e
                # Continue to retry loop
            # Note: json.JSONDecodeError or other processing errors after successful request
            # are handled inside the try block and return specific statuses without retry here.

            # If exception occurred and not the last attempt, wait and retry
            if last_exception is not None and attempt < max_retries - 1:
                print(f"Retrying status check in {retry_delay} seconds...")
                time.sleep(retry_delay)
                retry_delay *= 2
            elif last_exception is not None: # Max retries reached after an exception
                 print(f"Max retries ({max_retries}) reached for status check due to network errors. Last error: {last_exception}")
                 return {"taskStatus": "error", "error": f"Network Error after retries: {last_exception}"} # <<< Return error after retries
        
        # This point should theoretically not be reached if the loop handles all cases, but as a fallback:
        print(f"Status check loop completed unexpectedly after {max_retries} attempts.")
        return {"taskStatus": "error", "error": f"Status check failed after {max_retries} attempts. Last error: {last_exception}"}


# <<< Add NODE_CLASS_MAPPINGS and NODE_DISPLAY_NAME_MAPPINGS
NODE_CLASS_MAPPINGS = {
    "RH_ExecuteNode": ExecuteNode
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "RH_ExecuteNode": "RunningHub Execute Task"
}

# <<< Standard Python entry point check (optional but good practice)
if __name__ == "__main__":
    # Example usage or testing code could go here
    pass

