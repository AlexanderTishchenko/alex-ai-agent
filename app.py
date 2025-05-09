from flask import Flask, redirect, url_for, render_template, request, Response, stream_with_context, jsonify, session
from dotenv import load_dotenv
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required as _login_required, current_user
from authlib.integrations.flask_client import OAuth
import asyncio
import uuid
from queue import Queue, Empty
import json
import threading
import os
from pathlib import Path
import sys
import threading, webbrowser
# Import the refactored agent runner
from src.mcp_client_cli.agent_runner import AgentRunner
from src.secure_config import secure_config
from src.scheduler import bootstrap as scheduler_bootstrap
from src.tasks_routes import tasks_bp

load_dotenv()  # Load environment variables from .env

app = Flask(__name__, static_url_path="/static")
app.template_folder = os.path.join(os.path.dirname(__file__), 'templates')
app.static_folder = os.path.join(os.path.dirname(__file__), 'static')
app.secret_key = os.environ["FLASK_SECRET_KEY"]

# --- Flask-Login setup ---
login_manager = LoginManager(app)
login_manager.login_view = "login"

class User(UserMixin):
    """On production, store users in a DB."""
    def __init__(self, id_, email, name, picture):
        self.id = id_
        self.email = email
        self.name = name
        self.picture = picture

users: dict[str, User] = {}

@login_manager.user_loader
def load_user(user_id):
    return users.get(user_id)

# Feature flag for Google OAuth
ENABLE_GOOGLE_OAUTH = os.getenv("ENABLE_GOOGLE_OAUTH", "true").lower() in ("1", "true", "yes")
if ENABLE_GOOGLE_OAUTH:
    login_required = _login_required
else:
    def login_required(fn):
        return fn

# --- OAuth (Authlib) setup ---
if ENABLE_GOOGLE_OAUTH:
    oauth = OAuth(app)
    google = oauth.register(
        name="google",
        client_id=os.environ["GOOGLE_CLIENT_ID"],
        client_secret=os.environ["GOOGLE_CLIENT_SECRET"],
        server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",

        # access_token_url="https://oauth2.googleapis.com/token",
        # authorize_url="https://accounts.google.com/o/oauth2/v2/auth",
        api_base_url="https://www.googleapis.com/oauth2/v2/",
        client_kwargs={"scope": "openid email profile"},
    )
    print("CID:", google.client_id)
    print("CSecret set:", bool(os.environ.get("GOOGLE_CLIENT_SECRET")))

    @app.route("/login")
    def login():
        redirect_uri = url_for("auth_callback", _external=True)
        return google.authorize_redirect(redirect_uri)

    @app.route("/auth/google/callback")
    def auth_callback():
        token = google.authorize_access_token()
        resp = google.get("userinfo")
        info = resp.json()
        uid = info["id"]
        users[uid] = User(uid, info["email"], info["name"], info["picture"])
        login_user(users[uid])
        session["session_id"] = session.get("session_id") or uid
        return redirect(url_for("index"))

    @app.route("/logout")
    @login_required
    def logout():
        logout_user()
        return redirect(url_for("index"))
# end oauth feature toggle

# Dictionary to hold session-specific queues for SSE
session_queues: dict[str, Queue] = {}
# Dictionary to hold references to agent background tasks
agent_tasks: dict[str, threading.Thread] = {}

def run_async_in_thread(loop, coro):
    asyncio.set_event_loop(loop)
    loop.run_until_complete(coro)

# def sse_with_error_handling(fn):
#     def wrapper(*args, **kwargs):
#         def gen():
#             session_id = request.json.get("session_id") or kwargs.get("session_id")
#             try:
#                 # run the normal SSE generator
#                 for event in fn(*args, **kwargs):
#                     yield event
#             except Exception as e:
#                 # send the error as a chat message
#                 payload = {
#                     "type": "message",
#                     "content": f"⚠️ Internal error: {e}",
#                     "session_id": session_id
#                 }
#                 yield f"data: {json.dumps(payload)}\n\n"
#             finally:
#                 # always close the spinner
#                 payload = {"type": "end", "session_id": session_id}
#                 yield f"data: {json.dumps(payload)}\n\n"
#         return Response(gen(), mimetype="text/event-stream")
#     return wrapper

@app.route('/')
def index():
    if ENABLE_GOOGLE_OAUTH and not current_user.is_authenticated:
        return render_template('login.html')
    # Generate a unique session ID for each visit
    if 'session_id' not in session:
        session['session_id'] = str(uuid.uuid4())
    session_id = session['session_id']
    if session_id not in session_queues:
        session_queues[session_id] = Queue()
        print(f"Created queue for session: {session_id}")
    return render_template('index.html', session_id=session_id, user=current_user)

@app.route('/send_message', methods=['POST'])
@login_required
#@sse_with_error_handling
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
@login_required
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
                try:
                    item = q.get(timeout=30) # Check every 30 seconds
                    if item is None: # End signal
                        print(f"[Stream {session_id}] End signal received.")
                        yield f"data: {json.dumps({'type': 'status', 'content': 'Finished', 'session_id': session_id})}\n\n"
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
        except Exception as e:
            print(f"[Stream {session_id}] Unhandled error: {e}")
            payload = {'type': 'message', 'content': f' Internal error: {e}', 'session_id': session_id}
            yield f"data: {json.dumps(payload)}\n\n"
        finally:
            # Always send a final Finished status to close client spinner
            yield f"data: {json.dumps({'type': 'status', 'content': 'Finished', 'session_id': session_id})}\n\n"
            print(f"[Stream {session_id}] Cleaning up queue.")

    return Response(event_stream(), mimetype='text/event-stream')

@app.route('/check_telegram_creds', methods=['GET'])
@login_required
def check_telegram_creds():
    """Check if Telegram credentials exist."""
    return jsonify({
        'exists': secure_config.credentials_exist()
    })

@app.route('/save_telegram_creds', methods=['POST'])
@login_required
def save_telegram_creds():
    """Save Telegram credentials."""
    data = request.json
    api_id = data.get('api_id')
    api_hash = data.get('api_hash')
    
    if not api_id or not api_hash:
        return jsonify({
            'detail': 'API ID and API Hash are required'
        }), 400
    
    try:
        api_id = int(api_id)
    except ValueError:
        return jsonify({
            'detail': 'API ID must be a valid integer'
        }), 400
    
    if secure_config.save_credentials(api_id, api_hash):
        return jsonify({
            'status': 'Credentials saved successfully'
        })
    else:
        return jsonify({
            'detail': 'Failed to save credentials'
        }), 500

# --- Settings API endpoints ---
@app.route('/api/settings', methods=['GET'])
def get_settings():
    config_path = os.path.join(os.path.dirname(__file__), 'mcp-server-config.json')
    try:
        with open(config_path) as f:
            config = json.load(f)
        vault_args = config.get('mcpServers', {}).get('obsidian', {}).get('args', [])
        vault_path = vault_args[-1] if vault_args else ''
        model = config.get('llm', {}).get('model', '')
        openai_api_key = os.getenv('OPENAI_API_KEY', '')
        return jsonify({'vault_path': vault_path, 'model': model, 'openai_api_key': openai_api_key})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/settings', methods=['POST'])
def update_settings():
    data = request.get_json() or {}
    vault_path = data.get('vault_path')
    model = data.get('model')
    openai_api_key = data.get('openai_api_key')
    config_path = os.path.join(os.path.dirname(__file__), 'mcp-server-config.json')
    try:
        with open(config_path) as f:
            config = json.load(f)
        obs_conf = config.setdefault('mcpServers', {}).setdefault('obsidian', {})
        args = obs_conf.get('args', [])
        prefix = args[:-1] if len(args) >= 1 else []
        obs_conf['args'] = prefix + [vault_path]
        config.setdefault('llm', {})['model'] = model
        with open(config_path, 'w') as f:
            json.dump(config, f, indent=2)
        # override runtime API key
        os.environ['OPENAI_API_KEY'] = openai_api_key or ''
        os.environ['LLM_API_KEY'] = openai_api_key or ''
        return jsonify({'status': 'ok'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# --- Register scheduler and tasks blueprint ---
scheduler_bootstrap(app)
app.register_blueprint(tasks_bp, url_prefix='/api')

@app.route('/scheduler')
@login_required
def scheduler_page():
    return render_template('scheduler.html')

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
    
    # Automatically open default web browser to scheduler page
    threading.Timer(1, lambda: webbrowser.open("http://127.0.0.1:5001/")).start()

    # Consider using a more production-ready server like gunicorn or waitress
    # For development:
    app.run(debug=True, port=5001, threaded=True) # Use threaded for background tasks