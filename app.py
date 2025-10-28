from flask import Flask, render_template
from datetime import datetime

app = Flask(__name__)

@app.route("/")
def home():
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return render_template("index.html", time=now)

if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)
