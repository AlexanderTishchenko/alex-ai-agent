from flask import Flask, render_template, request, Response, stream_with_context, jsonify, session
import asyncio
import uuid
from queue import Queue, Empty
import json
import threading
import os
from pathlib import Path
import sys

# Import the refactored agent runner
from src.mcp_client_cli.agent_runner import AgentRunner  

# Configure Flask app
app = Flask(__name__)
# Make sure templates and static folders are relative to app.py location
app.template_folder = os.path.join(os.path.dirname(__file__), 'templates')
app.static_folder = os.path.join(os.path.dirname(__file__), 'static')
app.secret_key = os.urandom(24) # Needed for session management

# Dictionary to hold session-specific queues for SSE
session_queues: dict[str, Queue] = {}
# Dictionary to hold references to agent background tasks
agent_tasks: dict[str, threading.Thread] = {}

def run_async_in_thread(loop, coro):
    asyncio.set_event_loop(loop)
    loop.run_until_complete(coro)

@app.route('/')
def index():
    # Generate a unique session ID for each new visit
    if 'session_id' not in session:
        session['session_id'] = str(uuid.uuid4())
    session_id = session['session_id']
    
    # Create a queue for this session if it doesn't exist
    if session_id not in session_queues:
        session_queues[session_id] = Queue()
        print(f"Created queue for session: {session_id}")
        
    return render_template('index.html', session_id=session_id)

@app.route('/send_message', methods=['POST'])
def send_message():
    data = request.json
    user_message = data.get('message')
    session_id = data.get('session_id')

    if not user_message or not session_id:
        return jsonify({"error": "Message or session_id missing"}), 400
        
    if session_id not in session_queues:
         # If queue doesn't exist, maybe the session expired or it's the first message 
         # Let's create it here as well to be safe
         session_queues[session_id] = Queue()
         print(f"Created queue for session {session_id} in send_message")
         # return jsonify({"error": "Invalid session_id"}), 400 # Removed error

    output_queue = session_queues[session_id]
    
    # Check if an agent task is already running for this session
    if session_id in agent_tasks and agent_tasks[session_id].is_alive():
         return jsonify({"error": "Agent is currently busy. Please wait."}), 429 
        
    print(f"[App {session_id}] Received message: {user_message}")

    # Instantiate AgentRunner with the specific queue for this request/session
    # This ensures config is loaded relatively fresh and toolkits are managed per run
    agent_runner = AgentRunner(output_queue=output_queue)

    # Run the agent logic in a separate thread to avoid blocking Flask
    new_loop = asyncio.new_event_loop()
    # Assuming the web UI doesn't need complex continuation logic like the CLI 'c' prefix yet
    # Pass is_continuation=False for now. This could be enhanced later.
    agent_coro = agent_runner.run(user_message, session_id, is_continuation=False)
    
    thread = threading.Thread(target=run_async_in_thread, args=(new_loop, agent_coro), daemon=True)
    agent_tasks[session_id] = thread
    thread.start()

    return jsonify({"status": "Message received, processing started"})

# --- Placeholder for Tool Confirmation Route ---
# @app.route('/confirm_tool', methods=['POST'])
# def confirm_tool():
#     data = request.json
#     session_id = data.get('session_id')
#     confirmed = data.get('confirmed')
#     tool_name = data.get('tool_name')
#     # --- Logic to signal the waiting agent thread ---
#     # This requires inter-thread communication, e.g., another queue or event
#     print(f"[App {session_id}] Received confirmation for {tool_name}: {confirmed}")
#     # Signal the agent_runner.run(...) function to proceed
#     return jsonify({"status": "Confirmation received"})

@app.route('/stream/<session_id>')
def stream(session_id):
    if session_id not in session_queues:
        print(f"[Stream {session_id}] Error: No queue found for session.")
        # Return an empty response or an error event
        def error_gen():
             yield f"data: {json.dumps({'type': 'error', 'content': 'Invalid session ID or session expired.'})}\n\n"
        return Response(error_gen(), mimetype='text/event-stream')

    print(f"[Stream {session_id}] Client connected.")
    
    def event_stream():
        q = session_queues[session_id]
        try:
            while True:
                # Blocking wait with timeout
                try:
                    item = q.get(timeout=30) # Check every 30 seconds
                    if item is None: # End signal
                        print(f"[Stream {session_id}] End signal received.")
                        yield f"data: {json.dumps({'type': 'status', 'content': 'Finished'})}\n\n"
                        # Remove agent task reference
                        if session_id in agent_tasks:
                            del agent_tasks[session_id]
                        break 
                    print(f"[Stream {session_id}] Yielding: {item}")
                    # Ensure the item is intended for this session (it should be by design here)
                    if item.get("session_id") == session_id:
                        yield f"data: {json.dumps(item)}\n\n"
                    else:
                         print(f"[Stream {session_id}] Warning: Received item for wrong session {item.get('session_id')}")
                except Empty:
                    # Timeout reached, send keep-alive comment or just continue loop
                     yield ": keepalive\n\n" 
                    # Check if the agent task is still alive (optional)
                    # if session_id in agent_tasks and not agent_tasks[session_id].is_alive():
                    #     print(f"[Stream {session_id}] Agent task finished unexpectedly.")
                    #     break
        except GeneratorExit:
             print(f"[Stream {session_id}] Client disconnected.")
        finally:
            # Clean up when client disconnects or stream ends
            print(f"[Stream {session_id}] Cleaning up queue.")
            # Clear the queue for this session to avoid memory leaks if client disconnects
            # while q.qsize() > 0:
            #     try: q.get_nowait()
            #     except Empty: break
            # Optionally remove the queue entirely if session management dictates
            # if session_id in session_queues:
            #      del session_queues[session_id]
            pass # Keep queue for potential reconnection unless explicitly managed otherwise

    return Response(event_stream(), mimetype='text/event-stream')

if __name__ == '__main__':
    # Ensure templates and static directories exist (optional, Flask usually handles this)
    # Use absolute paths based on the script's location
    base_dir = os.path.dirname(os.path.abspath(__file__))
    Path(os.path.join(base_dir, 'templates')).mkdir(exist_ok=True)
    Path(os.path.join(base_dir, 'static')).mkdir(exist_ok=True)
    
    # Add src directory to Python path if running app.py directly from root
    src_path = os.path.join(base_dir, 'src')
    if src_path not in sys.path:
        sys.path.insert(0, src_path)
        print(f"Added {src_path} to sys.path")

    # Needs python-dotenv if you use .env for API keys
    from dotenv import load_dotenv
    load_dotenv() # Load variables from .env file
    
    # Consider using a more production-ready server like gunicorn or waitress
    # For development:
    app.run(debug=True, port=5001, threaded=True) # Use threaded for background tasks 