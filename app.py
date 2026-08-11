from flask import Flask, render_template, request, jsonify, send_from_directory
import uuid
import os

app = Flask(__name__, static_folder='static', template_folder='templates')

# In-memory storage for rooms
ROOMS = {}

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/manifest.json")
def manifest():
    return send_from_directory('static', 'manifest.json', mimetype='application/json')

@app.route("/sw.js")
def service_worker():
    response = send_from_directory('static', 'sw.js', mimetype='application/javascript')
    response.headers['Service-Worker-Allowed'] = '/'
    return response

# --- API ENDPOINTS ---

@app.route("/api/list_rooms", methods=["GET"])
def list_rooms():
    active_rooms = []
    for code, room in ROOMS.items():
        active_rooms.append({
            "code": code,
            "seats": room["seats"],
            "status": room["status"],
            "players_count": len(room["players"]),
            "active_count": len([p for p in room["players"].values() if p["status"] == "active"])
        })
    return jsonify({"rooms": active_rooms})

@app.route("/api/create_room", methods=["POST"])
def create_room():
    data = request.json or {}
    room_code = data.get("room_code", "").strip().upper()
    password = data.get("password", "").strip()
    seats = int(data.get("seats", 3))
    admin_name = data.get("admin_name", "Игрок 1").strip()

    if not room_code or not password:
        return jsonify({"error": "Укажите код бункера и пароль"}), 400

    if room_code in ROOMS:
        return jsonify({"error": "Бункер с таким кодом уже существует"}), 400

    admin_id = str(uuid.uuid4())
    ROOMS[room_code] = {
        "code": room_code,
        "password": password,
        "seats": seats,
        "status": "lobby", # 'lobby', 'voting', 'round_results', 'finished'
        "round": 1,
        "admin_id": admin_id,
        "players": {
            admin_id: {"id": admin_id, "name": admin_name, "status": "active", "voted_for": None}
        },
        "last_eliminated": None,
        "round_votes_summary": {}
    }

    return jsonify({
        "success": True,
        "room_code": room_code,
        "player_id": admin_id,
        "is_admin": True
    })

@app.route("/api/join_room", methods=["POST"])
def join_room():
    data = request.json or {}
    room_code = data.get("room_code", "").strip().upper()
    password = data.get("password", "").strip()
    player_name = data.get("player_name", "").strip()

    if room_code not in ROOMS:
        return jsonify({"error": "Бункер с таким кодом не найден"}), 404

    room = ROOMS[room_code]
    if room["password"] != password:
        return jsonify({"error": "Неверный пароль к бункеру"}), 403

    if not player_name:
        return jsonify({"error": "Укажите ваше имя"}), 400

    player_id = str(uuid.uuid4())
    room["players"][player_id] = {
        "id": player_id,
        "name": player_name,
        "status": "active",
        "voted_for": None
    }

    return jsonify({
        "success": True,
        "room_code": room_code,
        "player_id": player_id,
        "is_admin": False
    })

@app.route("/api/room_status/<room_code>", methods=["GET"])
def room_status(room_code):
    room_code = room_code.upper()
    player_id = request.args.get("player_id")

    if room_code not in ROOMS:
        return jsonify({"error": "Бункер был удален или не существует", "code": "ROOM_DELETED"}), 404

    room = ROOMS[room_code]

    if player_id and player_id not in room["players"]:
        return jsonify({"error": "Вы были исключены из бункера", "code": "PLAYER_KICKED"}), 403

    active_players = [p for p in room["players"].values() if p["status"] == "active"]
    voted_count = len([p for p in active_players if p["voted_for"] is not None])

    return jsonify({
        "code": room["code"],
        "seats": room["seats"],
        "status": room["status"],
        "round": room["round"],
        "admin_id": room["admin_id"],
        "players": list(room["players"].values()),
        "active_count": len(active_players),
        "voted_count": voted_count,
        "last_eliminated": room["last_eliminated"],
        "round_votes_summary": room["round_votes_summary"]
    })

@app.route("/api/kick_player", methods=["POST"])
def kick_player():
    data = request.json or {}
    room_code = data.get("room_code", "").upper()
    admin_id = data.get("admin_id")
    target_id = data.get("target_id")

    if room_code not in ROOMS:
        return jsonify({"error": "Бункер не найден"}), 404

    room = ROOMS[room_code]
    if room["admin_id"] != admin_id:
        return jsonify({"error": "Только создатель бункера может удалять игроков"}), 403

    if target_id == admin_id:
        return jsonify({"error": "Создатель не может удалить себя"}), 400

    if target_id in room["players"]:
        del room["players"][target_id]

    return jsonify({"success": True})

@app.route("/api/delete_room", methods=["POST"])
def delete_room():
    data = request.json or {}
    room_code = data.get("room_code", "").upper()
    admin_id = data.get("admin_id")

    if room_code not in ROOMS:
        return jsonify({"error": "Бункер не найден"}), 404

    room = ROOMS[room_code]
    if room["admin_id"] != admin_id:
        return jsonify({"error": "Только создатель бункера может удалить бункер"}), 403

    del ROOMS[room_code]
    return jsonify({"success": True})

@app.route("/api/start_game", methods=["POST"])
def start_game():
    data = request.json or {}
    room_code = data.get("room_code", "").upper()
    player_id = data.get("player_id")

    if room_code not in ROOMS:
        return jsonify({"error": "Бункер не найден"}), 404

    room = ROOMS[room_code]
    if room["admin_id"] != player_id:
        return jsonify({"error": "Только создатель может начать игру"}), 403

    active_players = [p for p in room["players"].values() if p["status"] == "active"]
    if len(active_players) <= room["seats"]:
        return jsonify({"error": f"Количество участников ({len(active_players)}) должно быть больше мест в бункере ({room['seats']})"}), 400

    room["status"] = "voting"
    room["round"] = 1
    room["last_eliminated"] = None
    for p in room["players"].values():
        p["voted_for"] = None

    return jsonify({"success": True})

@app.route("/api/cast_vote", methods=["POST"])
def cast_vote():
    data = request.json or {}
    room_code = data.get("room_code", "").upper()
    voter_id = data.get("voter_id")
    target_id = data.get("target_id")

    if room_code not in ROOMS:
        return jsonify({"error": "Бункер не найден"}), 404

    room = ROOMS[room_code]
    if room["status"] != "voting":
        return jsonify({"error": "Голосование сейчас не проводится"}), 400

    voter = room["players"].get(voter_id)
    if not voter or voter["status"] != "active":
        return jsonify({"error": "Выбывшие игроки не могут голосовать"}), 403

    target = room["players"].get(target_id)
    if not target or target["status"] != "active":
        return jsonify({"error": "Нельзя голосовать против выбывшего игрока"}), 400

    if voter_id == target_id:
        return jsonify({"error": "Нельзя голосовать против себя"}), 400

    voter["voted_for"] = target_id
    return jsonify({"success": True})

@app.route("/api/tally_votes", methods=["POST"])
def tally_votes():
    data = request.json or {}
    room_code = data.get("room_code", "").upper()
    player_id = data.get("player_id")

    if room_code not in ROOMS:
        return jsonify({"error": "Бункер не найден"}), 404

    room = ROOMS[room_code]
    if room["admin_id"] != player_id:
        return jsonify({"error": "Только создатель бункера может подводить итоги"}), 403

    active_players = [p for p in room["players"].values() if p["status"] == "active"]
    tally = {p["id"]: 0 for p in active_players}

    for p in active_players:
        if p["voted_for"] in tally:
            tally[p["voted_for"]] += 1

    # Find max voted
    max_votes = -1
    eliminated_id = None
    for pid, count in tally.items():
        if count > max_votes:
            max_votes = count
            eliminated_id = pid

    eliminated_player = room["players"].get(eliminated_id)
    if eliminated_player:
        eliminated_player["status"] = "eliminated"
        room["last_eliminated"] = eliminated_player["name"]

    # Summary
    summary = {room["players"][pid]["name"]: count for pid, count in tally.items()}
    room["round_votes_summary"] = summary

    # Check if game over
    remaining_active = [p for p in room["players"].values() if p["status"] == "active"]
    if len(remaining_active) <= room["seats"]:
        room["status"] = "finished"
    else:
        room["status"] = "round_results"

    return jsonify({"success": True, "eliminated": room["last_eliminated"]})

@app.route("/api/next_round", methods=["POST"])
def next_round():
    data = request.json or {}
    room_code = data.get("room_code", "").upper()
    player_id = data.get("player_id")

    if room_code not in ROOMS:
        return jsonify({"error": "Бункер не найден"}), 404

    room = ROOMS[room_code]
    if room["admin_id"] != player_id:
        return jsonify({"error": "Только создатель бункера может запускать следующий раунд"}), 403

    room["round"] += 1
    room["status"] = "voting"
    for p in room["players"].values():
        p["voted_for"] = None

    return jsonify({"success": True})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
