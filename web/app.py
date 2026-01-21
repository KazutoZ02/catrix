from flask import Flask, render_template, request, jsonify
import json, os

STATE_FILE = "state.json"

app = Flask(__name__)

def load_state():
    with open(STATE_FILE, "r") as f:
        return json.load(f)

def save_state(data):
    with open(STATE_FILE, "w") as f:
        json.dump(data, f, indent=2)

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/api/state")
def state():
    return jsonify(load_state())

@app.route("/api/update", methods=["POST"])
def update():
    state = load_state()
    payload = request.json

    for key, value in payload.items():
        state[key] = value

    save_state(state)
    return {"ok": True}

if __name__ == "__main__":
    app.run(
        host=os.getenv("WEB_HOST", "0.0.0.0"),
        port=int(os.getenv("WEB_PORT", 5000)),
        debug=False
    )
