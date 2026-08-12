from flask import Flask, render_template, request, jsonify, send_from_directory
import uuid
import os
import json
import tempfile
import time
from urllib.parse import unquote

app = Flask(__name__, static_folder='static', template_folder='templates')

TEMP_DIR = tempfile.gettempdir()
ROOMS_FILE = os.path.join(TEMP_DIR, "bunker_rooms_v4.json")

def load_rooms():
    if os.path.exists(ROOMS_FILE):
        try:
            with open(ROOMS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print("Load rooms error:", e)
            return {}
    return {}

def save_rooms():
    try:
        with open(ROOMS_FILE, "w", encoding="utf-8") as f:
            json.dump(ROOMS, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print("Save rooms error:", e)

# Storage for rooms (DB File)
ROOMS = load_rooms()

def cleanup_expired_rooms():
    global ROOMS
    now = time.time()
    expired = [code for code, r in ROOMS.items() if r.get("expires_at") and now > r["expires_at"]]
    if expired:
        for code in expired:
            del ROOMS[code]
        save_rooms()

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/manifest.json")
def manifest():
    response = send_from_directory('static', 'manifest.json', mimetype='application/json')
    response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    return response

@app.route("/sw.js")
def service_worker():
    response = send_from_directory('static', 'sw.js', mimetype='application/javascript')
    response.headers['Service-Worker-Allowed'] = '/'
    response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'
    return response

# --- API ENDPOINTS ---

@app.route("/api/list_rooms", methods=["GET"])
def list_rooms():
    global ROOMS
    ROOMS = load_rooms()
    cleanup_expired_rooms()
    active_rooms = []
    for code, room in ROOMS.items():
        if room.get("deleted"):
            continue
        clean_code = unquote(code)
        active_rooms.append({
            "code": clean_code,
            "seats": room["seats"],
            "status": room["status"],
            "players_count": len(room["players"]),
            "active_count": len([p for p in room["players"].values() if p["status"] == "active"])
        })
    return jsonify({"rooms": active_rooms})

@app.route("/api/create_room", methods=["POST"])
def create_room():
    global ROOMS
    ROOMS = load_rooms()
    cleanup_expired_rooms()
    data = request.json or {}
    raw_code = data.get("room_code", "").strip()
    room_code = unquote(raw_code).upper()
    password = data.get("password", "").strip()
    seats = int(data.get("seats", 3))
    lifetime_hours = float(data.get("lifetime_hours", 2.0))
    admin_name = data.get("admin_name", "Игрок 1").strip()[:16]
    avatar = data.get("avatar", "🦁")

    if not room_code or not password:
        return jsonify({"error": "Укажите код бункера и пароль"}), 400

    created_at = time.time()
    expires_at = created_at + (lifetime_hours * 3600)

    admin_id = str(uuid.uuid4())
    ROOMS[room_code] = {
        "code": room_code,
        "password": password,
        "seats": seats,
        "status": "lobby",
        "round": 1,
        "created_at": created_at,
        "expires_at": expires_at,
        "deleted": False,
        "admin_id": admin_id,
        "players": {
            admin_id: {"id": admin_id, "name": admin_name, "avatar": avatar, "status": "active", "voted_for": None}
        },
        "last_eliminated": None,
        "round_votes_summary": {}
    }
    save_rooms()

    return jsonify({
        "success": True,
        "room_code": room_code,
        "player_id": admin_id,
        "is_admin": True,
        "room_data": {
            "code": room_code,
            "seats": seats,
            "status": "lobby",
            "round": 1,
            "admin_id": admin_id,
            "players": list(ROOMS[room_code]["players"].values()),
            "active_count": 1,
            "voted_count": 0,
            "last_eliminated": None,
            "round_votes_summary": {}
        }
    })

@app.route("/api/join_room", methods=["POST"])
def join_room():
    global ROOMS
    ROOMS = load_rooms()
    cleanup_expired_rooms()
    data = request.json or {}
    raw_code = data.get("room_code", "").strip()
    room_code = unquote(raw_code).upper()
    password = data.get("password", "").strip()
    player_name = data.get("player_name", "").strip()[:16]
    avatar = data.get("avatar", "🦊")

    if not room_code or not player_name:
        return jsonify({"error": "Заполните все поля"}), 400

    # Auto-recovery for serverless cold restarts
    if room_code not in ROOMS or ROOMS[room_code].get("deleted"):
        created_at = time.time()
        expires_at = created_at + (2 * 3600)
        admin_id = str(uuid.uuid4())
        ROOMS[room_code] = {
            "code": room_code,
            "password": password,
            "seats": 3,
            "status": "lobby",
            "round": 1,
            "created_at": created_at,
            "expires_at": expires_at,
            "deleted": False,
            "admin_id": admin_id,
            "players": {
                admin_id: {"id": admin_id, "name": player_name, "avatar": avatar, "status": "active", "voted_for": None}
            },
            "last_eliminated": None,
            "round_votes_summary": {}
        }
        save_rooms()
        return jsonify({
            "success": True,
            "room_code": room_code,
            "player_id": admin_id,
            "is_admin": True
        })

    room = ROOMS[room_code]
    if room["password"] and password and room["password"] != password:
        return jsonify({"error": "Неверный пароль к бункеру"}), 403

    # Reconnect logic: Check if player with same name already exists in room
    existing_player = None
    for p in room["players"].values():
        if p["name"].lower() == player_name.lower():
            existing_player = p
            break

    if existing_player:
        player_id = existing_player["id"]
        existing_player["name"] = player_name
        existing_player["avatar"] = avatar
        is_admin = (player_id == room["admin_id"])
        save_rooms()
        return jsonify({
            "success": True,
            "room_code": room_code,
            "player_id": player_id,
            "is_admin": is_admin,
            "reconnected": True
        })

    # New player
    player_id = str(uuid.uuid4())
    room["players"][player_id] = {
        "id": player_id,
        "name": player_name,
        "avatar": avatar,
        "status": "active",
        "voted_for": None
    }
    save_rooms()

    return jsonify({
        "success": True,
        "room_code": room_code,
        "player_id": player_id,
        "is_admin": False
    })

@app.route("/api/room_status/<room_code>", methods=["GET"])
def room_status(room_code):
    global ROOMS
    ROOMS = load_rooms()
    cleanup_expired_rooms()
    room_code = unquote(room_code).upper()
    player_id = request.args.get("player_id")

    if room_code in ROOMS and ROOMS[room_code].get("deleted"):
        return jsonify({"error": "Бункер был удален организатором", "code": "ROOM_DELETED"}), 404

    if room_code not in ROOMS:
        return jsonify({"error": "Бункер не найден", "code": "ROOM_NOT_FOUND"}), 404

    room = ROOMS[room_code]

    # Auto-restore player if missing on cold restart
    if player_id and player_id not in room["players"]:
        room["players"][player_id] = {
            "id": player_id,
            "name": "Игрок",
            "avatar": "👤",
            "status": "active",
            "voted_for": None
        }
        save_rooms()

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

@app.route("/api/update_profile", methods=["POST"])
def update_profile():
    global ROOMS
    ROOMS = load_rooms()
    data = request.json or {}
    room_code = unquote(data.get("room_code", "")).upper()
    player_id = data.get("player_id")
    new_name = data.get("name", "").strip()[:16]
    new_avatar = data.get("avatar", "").strip()

    if room_code not in ROOMS or ROOMS[room_code].get("deleted"):
        return jsonify({"error": "Бункер не найден"}), 404

    room = ROOMS[room_code]
    
    # Auto-register player if missing in room state
    if player_id not in room["players"]:
        room["players"][player_id] = {
            "id": player_id,
            "name": new_name if new_name else "Игрок",
            "avatar": new_avatar if new_avatar else "🦁",
            "status": "active",
            "voted_for": None
        }
    else:
        player = room["players"][player_id]
        if new_name:
            player["name"] = new_name
        if new_avatar:
            player["avatar"] = new_avatar

    save_rooms()
    return jsonify({"success": True})

@app.route("/api/update_room_settings", methods=["POST"])
def update_room_settings():
    global ROOMS
    ROOMS = load_rooms()
    data = request.json or {}
    room_code = unquote(data.get("room_code", "")).upper()
    admin_id = data.get("admin_id")
    new_seats = data.get("seats")
    new_lifetime = data.get("lifetime_hours")

    if room_code not in ROOMS or ROOMS[room_code].get("deleted"):
        return jsonify({"error": "Бункер не найден"}), 404

    room = ROOMS[room_code]
    if room["admin_id"] != admin_id:
        return jsonify({"error": "Только создатель бункера может изменять настройки"}), 403

    if new_seats is not None:
        try:
            room["seats"] = int(new_seats)
        except:
            pass

    if new_lifetime is not None:
        try:
            lifetime_hours = float(new_lifetime)
            room["expires_at"] = room["created_at"] + (lifetime_hours * 3600)
        except:
            pass

    save_rooms()
    return jsonify({"success": True})

@app.route("/api/change_password", methods=["POST"])
def change_password():
    global ROOMS
    ROOMS = load_rooms()
    data = request.json or {}
    room_code = unquote(data.get("room_code", "")).upper()
    admin_id = data.get("admin_id")
    new_password = data.get("new_password", "").strip()

    if room_code not in ROOMS or ROOMS[room_code].get("deleted"):
        return jsonify({"error": "Бункер не найден"}), 404

    room = ROOMS[room_code]
    if room["admin_id"] != admin_id:
        return jsonify({"error": "Только создатель бункера может менять пароль"}), 403

    if not new_password:
        return jsonify({"error": "Пароль не может быть пустым"}), 400

    room["password"] = new_password
    save_rooms()
    return jsonify({"success": True})

@app.route("/api/leave_room", methods=["POST"])
def leave_room_api():
    global ROOMS
    ROOMS = load_rooms()
    data = request.json or {}
    room_code = unquote(data.get("room_code", "")).upper()
    player_id = data.get("player_id")

    if room_code in ROOMS and player_id in ROOMS[room_code]["players"]:
        del ROOMS[room_code]["players"][player_id]
        save_rooms()

    return jsonify({"success": True})

@app.route("/api/delete_room", methods=["POST"])
def delete_room():
    global ROOMS
    ROOMS = load_rooms()
    data = request.json or {}
    room_code = unquote(data.get("room_code", "")).upper()
    admin_id = data.get("admin_id")

    if room_code not in ROOMS:
        return jsonify({"error": "Бункер не найден"}), 404

    room = ROOMS[room_code]
    if room["admin_id"] != admin_id:
        return jsonify({"error": "Только создатель бункера может удалить бункер"}), 403

    room["deleted"] = True
    save_rooms()
    return jsonify({"success": True})

@app.route("/api/start_game", methods=["POST"])
def start_game():
    global ROOMS
    ROOMS = load_rooms()
    data = request.json or {}
    room_code = unquote(data.get("room_code", "")).upper()
    player_id = data.get("player_id")

    if room_code not in ROOMS or ROOMS[room_code].get("deleted"):
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
    save_rooms()

    return jsonify({"success": True})

@app.route("/api/cast_vote", methods=["POST"])
def cast_vote():
    global ROOMS
    ROOMS = load_rooms()
    data = request.json or {}
    room_code = unquote(data.get("room_code", "")).upper()
    voter_id = data.get("voter_id")
    target_id = data.get("target_id")

    if room_code not in ROOMS or ROOMS[room_code].get("deleted"):
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
    save_rooms()
    return jsonify({"success": True})

@app.route("/api/tally_votes", methods=["POST"])
def tally_votes():
    global ROOMS
    ROOMS = load_rooms()
    data = request.json or {}
    room_code = unquote(data.get("room_code", "")).upper()
    player_id = data.get("player_id")

    if room_code not in ROOMS or ROOMS[room_code].get("deleted"):
        return jsonify({"error": "Бункер не найден"}), 404

    room = ROOMS[room_code]
    if room["admin_id"] != player_id:
        return jsonify({"error": "Только создатель бункера может подводить итоги"}), 403

    active_players = [p for p in room["players"].values() if p["status"] == "active"]
    tally = {p["id"]: 0 for p in active_players}

    for p in active_players:
        if p["voted_for"] in tally:
            tally[p["voted_for"]] += 1

    # Eliminate candidate with strictly the highest number of votes AGAINST
    max_votes = -1
    eliminated_id = None
    for pid, count in tally.items():
        if count > max_votes:
            max_votes = count
            eliminated_id = pid

    eliminated_player = room["players"].get(eliminated_id)
    if eliminated_player and max_votes > 0:
        eliminated_player["status"] = "eliminated"
        room["last_eliminated"] = f"{eliminated_player.get('avatar', '👤')} {eliminated_player['name']}"

    # Summary
    summary = {f"{room['players'][pid].get('avatar', '👤')} {room['players'][pid]['name']}": count for pid, count in tally.items()}
    room["round_votes_summary"] = summary

    # Check if game over
    remaining_active = [p for p in room["players"].values() if p["status"] == "active"]
    if len(remaining_active) <= room["seats"]:
        room["status"] = "finished"
    else:
        room["status"] = "round_results"

    save_rooms()
    return jsonify({"success": True, "eliminated": room["last_eliminated"]})

@app.route("/api/next_round", methods=["POST"])
def next_round():
    global ROOMS
    ROOMS = load_rooms()
    data = request.json or {}
    room_code = unquote(data.get("room_code", "")).upper()
    player_id = data.get("player_id")

    if room_code not in ROOMS or ROOMS[room_code].get("deleted"):
        return jsonify({"error": "Бункер не найден"}), 404

    room = ROOMS[room_code]
    if room["admin_id"] != player_id:
        return jsonify({"error": "Только создатель бункера может запускать следующий раунд"}), 403

    room["round"] += 1
    room["status"] = "voting"
    for p in room["players"].values():
        p["voted_for"] = None

    save_rooms()
    return jsonify({"success": True})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
