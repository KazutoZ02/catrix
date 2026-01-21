from flask import Flask, jsonify, request, render_template
import json, os

STATE_FILE = "state.json"

app = Flask(__name__)

def read_state():
    if not os.path.exists(STATE_FILE):
        return {"streams": {}, "personality": "cattrix"}
    with open(STATE_FILE) as f:
        return json.load(f)

def write_state(data):
    with open(STATE_FILE, "w") as f:
        json.dump(data, f, indent=2)

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/api/state")
def state():
    return jsonify(read_state())

@app.route("/api/personality", methods=["POST"])
def personality():
    data = read_state()
    data["personality"] = request.json["value"]
    write_state(data)
    return {"ok": True}

@app.route("/api/stream", methods=["POST"])
def add_stream():
    data = read_state()
    vid = request.json["video_id"]
    data["streams"][vid] = {"enabled": True}
    write_state(data)
    return {"ok": True}
