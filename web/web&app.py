from flask import Flask, jsonify, request, render_template
import json, os

STATE_FILE = "state.json"

app = Flask(__name__)

def load_state():
    if not os.path.exists(STATE_FILE):
        return {}
    with open(STATE_FILE, "r") as f:
        return json.load(f)

def save_state(data):
    with open(STATE_FILE, "w") as f:
        json.dump(data, f, indent=2)

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/api/state")
def get_state():
    return jsonify(load_state())

@app.route("/api/update", methods=["POST"])
def update_state():
    data = load_state()
    payload = request.json
    data.update(payload)
    save_state(data)
    return {"ok": True}

@app.route("/api/set_section", methods=["POST"])
def set_section():
    data = load_state()
    section = request.json["section"]
    value = request.json["value"]
    data[section] = value
    save_state(data)
    return {"ok": True}