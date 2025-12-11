from dotenv import load_dotenv
load_dotenv()  # Load environment variables from .env file

from app import app, socketio

if __name__ == '__main__':
    socketio.run(app, debug=True, port=1302, host='0.0.0.0',allow_unsafe_werkzeug=True)
