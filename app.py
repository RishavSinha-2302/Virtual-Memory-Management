"""
Flask web application — REST API and UI server for the
Virtual Memory Management Simulator.
"""

from __future__ import annotations

import glob
import json
import os
from pathlib import Path

from flask import Flask, jsonify, render_template, request, send_from_directory

from simulator.config import SimConfig
from simulator.engine import SimulationEngine

app = Flask(__name__)
engine = SimulationEngine()

SCENARIOS_DIR = Path(__file__).parent / "scenarios"


# ── Pages ───────────────────────────────────────────────────────────
@app.route("/")
def index():
    return render_template("index.html")


# ── API: simulation control ─────────────────────────────────────────
@app.route("/api/state", methods=["GET"])
def api_state():
    return jsonify(engine.get_full_state())


@app.route("/api/step", methods=["POST"])
def api_step():
    events = engine.step()
    return jsonify({"events": events, "state": engine.get_full_state()})


@app.route("/api/access", methods=["POST"])
def api_access():
    data = request.get_json(force=True)
    pid = int(data["pid"])
    vpn = int(data["vpn"])
    access_type = data.get("type", "read")
    events = engine.execute_access(pid, vpn, access_type)
    return jsonify({"events": events, "state": engine.get_full_state()})


@app.route("/api/reset", methods=["POST"])
def api_reset():
    data = request.get_json(force=True) if request.data else {}
    config_overrides = data.get("config")
    config = SimConfig()
    if config_overrides:
        for k, v in config_overrides.items():
            if hasattr(config, k):
                setattr(config, k, v)
    engine.reset(config)
    return jsonify({"status": "ok", "state": engine.get_full_state()})


# ── API: process management ─────────────────────────────────────────
@app.route("/api/process/create", methods=["POST"])
def api_create_process():
    data = request.get_json(force=True)
    name = data.get("name", "Process")
    regions = data.get("regions")
    proc = engine.create_process(name, regions)
    return jsonify({"process": proc, "state": engine.get_full_state()})


@app.route("/api/process/suspend", methods=["POST"])
def api_suspend_process():
    data = request.get_json(force=True)
    pid = int(data["pid"])
    events = engine.suspend_process(pid)
    return jsonify({"events": events, "state": engine.get_full_state()})


@app.route("/api/process/resume", methods=["POST"])
def api_resume_process():
    data = request.get_json(force=True)
    pid = int(data["pid"])
    events = engine.resume_process(pid)
    return jsonify({"events": events, "state": engine.get_full_state()})


# ── API: scenarios ──────────────────────────────────────────────────
@app.route("/api/scenarios", methods=["GET"])
def api_list_scenarios():
    """List all available scenario files."""
    scenarios = []
    for path in sorted(SCENARIOS_DIR.glob("*.json")):
        with open(path) as f:
            data = json.load(f)
        scenarios.append({
            "filename": path.name,
            "name": data.get("name", path.stem),
            "description": data.get("description", ""),
        })
    return jsonify({"scenarios": scenarios})


@app.route("/api/scenario/load", methods=["POST"])
def api_load_scenario():
    data = request.get_json(force=True)
    filename = data.get("filename")
    path = SCENARIOS_DIR / filename
    if not path.exists():
        return jsonify({"error": f"Scenario {filename} not found"}), 404
    name = engine.load_scenario_file(str(path))
    return jsonify({
        "status": "ok",
        "scenario_name": name,
        "state": engine.get_full_state(),
    })


# ── API: logs & timeline ───────────────────────────────────────────
@app.route("/api/log", methods=["GET"])
def api_log():
    n = int(request.args.get("n", 100))
    return jsonify({"log": engine.get_event_log(n)})


@app.route("/api/timeline", methods=["GET"])
def api_timeline():
    return jsonify({"timeline": engine.get_timeline()})


# ── Run ─────────────────────────────────────────────────────────────
if __name__ == "__main__":
    app.run(debug=True, port=5000)
