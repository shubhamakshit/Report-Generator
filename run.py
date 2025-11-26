from app import app

if __name__ == '__main__':
    app.run(debug=True, port=1302, host='0.0.0.0',threaded=True)
