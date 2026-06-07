"""
WhatsApp CRM Lite — app.py
Backend Flask complet : auth, webhook Green API, agent IA, paiements Mobile Money, dashboard admin + utilisateur
"""

import os, json, uuid, hashlib, hmac
from datetime import datetime, timedelta
from typing import Optional
from functools import wraps

import requests
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
from flask_jwt_extended import (
    JWTManager, create_access_token, jwt_required, get_jwt_identity, get_jwt
)
from supabase import create_client, Client
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()

# ─────────────────────────────────────────
# INIT
# ─────────────────────────────────────────
app = Flask(__name__, static_folder="static", template_folder=".")
CORS(app, origins=os.getenv("FRONTEND_URL", "*"))

app.config["JWT_SECRET_KEY"] = os.getenv("JWT_SECRET", "super-secret-change-me")
app.config["JWT_ACCESS_TOKEN_EXPIRES"] = timedelta(days=7)
jwt = JWTManager(app)

supabase: Client = create_client(
    os.getenv("SUPABASE_URL", ""),
    os.getenv("SUPABASE_SERVICE_KEY", "")
)
grok_client = OpenAI(
    api_key=os.getenv("GROK_API_KEY", ""),
    base_url="https://api.x.ai/v1",
)

ADMIN_SECRET = os.getenv("ADMIN_SECRET", "admin-secret-change-me")
API_URL      = os.getenv("API_URL", "http://localhost:5000")
FRONTEND_URL = os.getenv("FRONTEND_URL", "http://localhost:5000")


# ─────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────
def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        secret = request.headers.get("X-Admin-Secret", "")
        if secret != ADMIN_SECRET:
            return jsonify({"error": "Accès refusé"}), 403
        return f(*args, **kwargs)
    return decorated


def get_profile(user_id: str) -> Optional[dict]:
    res = supabase.table("profiles").select("*").eq("id", user_id).single().execute()
    return res.data


def order_number() -> str:
    return f"ORD-{int(datetime.utcnow().timestamp())}-{uuid.uuid4().hex[:4].upper()}"


# ─────────────────────────────────────────
# SERVE ONBOARDING PAGE
# ─────────────────────────────────────────
@app.route("/")
@app.route("/onboarding")
def onboarding():
    return send_from_directory(".", "onboarding.html")


# ═══════════════════════════════════════════════════════
# AUTH — Inscription & Connexion
# ═══════════════════════════════════════════════════════

@app.route("/api/auth/register", methods=["POST"])
def register():
    """Créer un compte boutique"""
    data = request.json or {}
    required = ["email", "password", "shop_name", "shop_type", "phone", "country"]
    if not all(data.get(k) for k in required):
        return jsonify({"error": "Champs manquants"}), 400

    try:
        # Créer l'utilisateur Supabase Auth
        auth_res = supabase.auth.sign_up({
            "email": data["email"],
            "password": data["password"],
        })
        user_id = auth_res.user.id

        # Créer le profil boutique
        supabase.table("profiles").insert({
            "id": user_id,
            "email": data["email"],
            "shop_name": data["shop_name"],
            "shop_type": data["shop_type"],
            "phone": data["phone"],
            "country": data["country"],
            "currency": data.get("currency", "XOF"),
            "plan": "trial",
            "is_active": True,
        }).execute()

        token = create_access_token(identity=user_id, additional_claims={"role": "shop"})
        return jsonify({"token": token, "user_id": user_id}), 201

    except Exception as e:
        return jsonify({"error": str(e)}), 400


@app.route("/api/auth/login", methods=["POST"])
def login():
    """Connexion boutique"""
    data = request.json or {}
    try:
        auth_res = supabase.auth.sign_in_with_password({
            "email": data["email"],
            "password": data["password"],
        })
        user_id = auth_res.user.id
        profile = get_profile(user_id)

        if not profile:
            return jsonify({"error": "Profil introuvable"}), 404
        if not profile.get("is_active"):
            return jsonify({"error": "Compte suspendu. Contactez le support."}), 403

        token = create_access_token(identity=user_id, additional_claims={"role": "shop"})
        return jsonify({"token": token, "profile": profile})

    except Exception as e:
        return jsonify({"error": "Email ou mot de passe incorrect"}), 401


# ═══════════════════════════════════════════════════════
# SHOP — Configuration boutique
# ═══════════════════════════════════════════════════════

@app.route("/api/shop/profile", methods=["GET"])
@jwt_required()
def get_shop_profile():
    uid = get_jwt_identity()
    profile = get_profile(uid)
    if not profile:
        return jsonify({"error": "Profil introuvable"}), 404
    # Masquer les clés sensibles
    profile.pop("payment_api_key", None)
    profile.pop("green_api_token", None)
    return jsonify(profile)


@app.route("/api/shop/profile", methods=["PATCH"])
@jwt_required()
def update_shop_profile():
    uid = get_jwt_identity()
    data = request.json or {}
    allowed = [
        "shop_name", "shop_type", "phone", "currency",
        "green_api_instance_id", "green_api_token",
        "payment_provider", "payment_api_key", "payment_site_id",
        "whatsapp_number",
    ]
    update_data = {k: v for k, v in data.items() if k in allowed}
    supabase.table("profiles").update(update_data).eq("id", uid).execute()
    return jsonify({"success": True})


# ═══════════════════════════════════════════════════════
# SHOP — Catalogue produits
# ═══════════════════════════════════════════════════════

@app.route("/api/shop/catalog", methods=["GET"])
@jwt_required()
def get_catalog():
    uid = get_jwt_identity()
    res = supabase.table("ai_catalog").select("*").eq("profile_id", uid).execute()
    return jsonify(res.data)


@app.route("/api/shop/catalog", methods=["POST"])
@jwt_required()
def add_catalog_item():
    uid = get_jwt_identity()
    data = request.json or {}
    item = {
        "profile_id": uid,
        "name": data.get("name"),
        "description": data.get("description"),
        "price": data.get("price"),
        "category": data.get("category"),
        "availability": data.get("availability", "available"),
        "keywords": data.get("keywords", []),
    }
    res = supabase.table("ai_catalog").insert(item).execute()
    return jsonify(res.data[0]), 201


@app.route("/api/shop/catalog/<item_id>", methods=["DELETE"])
@jwt_required()
def delete_catalog_item(item_id):
    uid = get_jwt_identity()
    supabase.table("ai_catalog").delete().eq("id", item_id).eq("profile_id", uid).execute()
    return jsonify({"success": True})


# ═══════════════════════════════════════════════════════
# SHOP — Dashboard utilisateur (stats, commandes, contacts)
# ═══════════════════════════════════════════════════════

@app.route("/api/shop/dashboard", methods=["GET"])
@jwt_required()
def shop_dashboard():
    """Stats complètes pour le dashboard utilisateur"""
    uid = get_jwt_identity()

    # Commandes
    orders_res = supabase.table("orders").select("*").eq("profile_id", uid).execute()
    orders = orders_res.data or []

    # Contacts
    contacts_res = supabase.table("contacts").select("*").eq("profile_id", uid).execute()
    contacts = contacts_res.data or []

    # Calculs revenus
    paid_orders   = [o for o in orders if o["payment_status"] == "paid"]
    total_revenue = sum(float(o["amount"]) for o in paid_orders)
    pending_orders = [o for o in orders if o["payment_status"] == "pending"]

    # Revenus par jour (30 derniers jours)
    from collections import defaultdict
    daily = defaultdict(float)
    for o in paid_orders:
        day = o["created_at"][:10]
        daily[day] += float(o["amount"])
    revenue_chart = [{"date": k, "amount": v} for k, v in sorted(daily.items())[-30:]]

    # Localisation clients (pour la carte)
    locations = [
        {"city": c.get("city"), "country": c.get("country"),
         "lat": c.get("latitude"), "lng": c.get("longitude"),
         "name": c.get("display_name", c["whatsapp_number"])}
        for c in contacts if c.get("city")
    ]

    # Commandes récentes
    recent_orders = sorted(orders, key=lambda x: x["created_at"], reverse=True)[:10]

    return jsonify({
        "stats": {
            "total_revenue": total_revenue,
            "total_orders": len(orders),
            "paid_orders": len(paid_orders),
            "pending_orders": len(pending_orders),
            "total_contacts": len(contacts),
            "conversion_rate": round(len(paid_orders) / max(len(contacts), 1) * 100, 1),
        },
        "revenue_chart": revenue_chart,
        "recent_orders": recent_orders,
        "client_locations": locations,
        "top_contacts": sorted(contacts, key=lambda x: float(x.get("total_spent") or 0), reverse=True)[:5],
    })


@app.route("/api/shop/orders", methods=["GET"])
@jwt_required()
def get_orders():
    uid = get_jwt_identity()
    status = request.args.get("status")
    query = supabase.table("orders").select("*, contacts(display_name,whatsapp_number)").eq("profile_id", uid)
    if status:
        query = query.eq("payment_status", status)
    res = query.order("created_at", desc=True).limit(50).execute()
    return jsonify(res.data)


@app.route("/api/shop/contacts", methods=["GET"])
@jwt_required()
def get_contacts():
    uid = get_jwt_identity()
    res = supabase.table("contacts").select("*").eq("profile_id", uid)\
        .order("last_contact_at", desc=True).limit(100).execute()
    return jsonify(res.data)


@app.route("/api/shop/conversations", methods=["GET"])
@jwt_required()
def get_conversations():
    uid = get_jwt_identity()
    res = supabase.table("conversations")\
        .select("*, contacts(display_name,whatsapp_number)")\
        .eq("profile_id", uid)\
        .order("updated_at", desc=True).limit(50).execute()
    return jsonify(res.data)


@app.route("/api/shop/conversations/<conv_id>/messages", methods=["GET"])
@jwt_required()
def get_messages(conv_id):
    uid = get_jwt_identity()
    res = supabase.table("messages")\
        .select("*").eq("conversation_id", conv_id)\
        .eq("profile_id", uid)\
        .order("created_at").execute()
    return jsonify(res.data)


# ═══════════════════════════════════════════════════════
# WEBHOOK — Green API (messages WhatsApp entrants)
# ═══════════════════════════════════════════════════════

@app.route("/webhook/green-api/<instance_id>", methods=["POST"])
def green_api_webhook(instance_id):
    """Point d'entrée des messages WhatsApp via Green API"""
    # Répondre immédiatement à Green API
    body = request.json or {}

    # Traitement asynchrone simulé (en prod: Celery ou thread)
    import threading
    t = threading.Thread(target=process_whatsapp_message, args=(instance_id, body))
    t.daemon = True
    t.start()

    return jsonify({"received": True}), 200


def process_whatsapp_message(instance_id: str, body: dict):
    """Traite un message WhatsApp entrant"""
    try:
        # 1. Trouver la boutique
        res = supabase.table("profiles")\
            .select("*").eq("green_api_instance_id", instance_id)\
            .eq("is_active", True).execute()
        if not res.data:
            return
        profile = res.data[0]

        # 2. Parser le message
        if body.get("typeWebhook") != "incomingMessageReceived":
            return
        msg_data = body.get("messageData", {})
        sender_data = body.get("senderData", {})
        text = (
            msg_data.get("textMessageData", {}).get("textMessage") or
            msg_data.get("extendedTextMessageData", {}).get("text") or ""
        )
        if not text:
            return

        chat_id = sender_data.get("chatId", "").replace("@c.us", "")
        sender_name = sender_data.get("senderName", "")

        # 3. Trouver ou créer le contact
        contact_res = supabase.table("contacts")\
            .select("*").eq("profile_id", profile["id"])\
            .eq("whatsapp_number", chat_id).execute()

        if contact_res.data:
            contact = contact_res.data[0]
        else:
            new_c = supabase.table("contacts").insert({
                "profile_id": profile["id"],
                "whatsapp_number": chat_id,
                "display_name": sender_name,
            }).execute()
            contact = new_c.data[0]

        # 4. Trouver ou créer conversation ouverte
        conv_res = supabase.table("conversations")\
            .select("*").eq("profile_id", profile["id"])\
            .eq("contact_id", contact["id"])\
            .eq("status", "open").execute()

        if conv_res.data:
            conversation = conv_res.data[0]
        else:
            new_conv = supabase.table("conversations").insert({
                "profile_id": profile["id"],
                "contact_id": contact["id"],
            }).execute()
            conversation = new_conv.data[0]

        # 5. Sauvegarder message entrant
        supabase.table("messages").insert({
            "conversation_id": conversation["id"],
            "profile_id": profile["id"],
            "direction": "inbound",
            "sender": "client",
            "content": text,
        }).execute()

        # 6. Charger le catalogue
        cat_res = supabase.table("ai_catalog")\
            .select("*").eq("profile_id", profile["id"])\
            .eq("availability", "available").execute()
        catalog = cat_res.data or []

        # 7. Appeler l'IA
        history = conversation.get("ai_conversation_context") or []
        ai_result = ai_agent(profile, contact, text, history, catalog)

        # 8. Mise à jour contact
        contact_update = {
            "last_contact_at": datetime.utcnow().isoformat(),
            "lead_score": ai_result.get("lead_score", contact.get("lead_score", 0)),
            "intent": ai_result.get("intent", "inconnu"),
            "last_message_summary": ai_result.get("conversation_summary", ""),
        }
        if ai_result.get("detected_city"):
            contact_update["city"] = ai_result["detected_city"]
        supabase.table("contacts").update(contact_update).eq("id", contact["id"]).execute()

        # 9. Mettre à jour contexte conversation
        new_history = history + [
            {"role": "user", "content": text},
            {"role": "assistant", "content": ai_result["message"]},
        ]
        supabase.table("conversations").update({
            "ai_conversation_context": new_history[-20:],
            "updated_at": datetime.utcnow().isoformat(),
        }).eq("id", conversation["id"]).execute()

        # 10. Envoyer la réponse IA via Green API
        send_whatsapp_message(profile["green_api_instance_id"], profile["green_api_token"], chat_id, ai_result["message"])

        # 11. Sauvegarder message sortant
        supabase.table("messages").insert({
            "conversation_id": conversation["id"],
            "profile_id": profile["id"],
            "direction": "outbound",
            "sender": "ai",
            "content": ai_result["message"],
        }).execute()

        # 12. Créer commande + lien de paiement si besoin
        if ai_result.get("wants_to_order") and float(ai_result.get("order_amount", 0)) > 0:
            create_order_and_send_link(profile, contact, conversation, ai_result, chat_id)

    except Exception as e:
        print(f"[WEBHOOK ERROR] {e}")


# ═══════════════════════════════════════════════════════
# SERVICE — Agent IA (Claude)
# ═══════════════════════════════════════════════════════

def ai_agent(profile: dict, contact: dict, message: str, history: list, catalog: list) -> dict:
    """Génère une réponse IA commerciale"""

    catalog_text = ""
    if catalog:
        lines = [f"- {p['name']}: {int(p['price']):,} {profile.get('currency','XOF')} — {p.get('description','')}" for p in catalog]
        catalog_text = "\nPRODUITS DISPONIBLES:\n" + "\n".join(lines)

    system = f"""Tu es l'assistant commercial IA de "{profile['shop_name']}" sur WhatsApp.
Rôle: qualifier, convaincre, vendre et gérer les objections. Sois chaleureux et concis (max 3 phrases).
Si le client veut acheter, extrais le montant et la description.
Essaie de détecter naturellement la ville du client.{catalog_text}

Client: {contact.get('display_name') or contact['whatsapp_number']}
Historique: {contact.get('total_orders',0)} commandes, {contact.get('total_spent',0)} {profile.get('currency','XOF')} dépensés.

Réponds UNIQUEMENT en JSON:
{{"message":"réponse WhatsApp","intent":"achat|info|support|spam|inconnu","lead_score":0,"detected_city":null,"wants_to_order":false,"order_description":null,"order_amount":0,"conversation_summary":"résumé 1 phrase"}}"""

    messages_payload = [{"role": "system", "content": system}] + (history or [])[-10:] + [{"role": "user", "content": message}]

    try:
        resp = grok_client.chat.completions.create(
            model="grok-3",
            max_tokens=600,
            messages=messages_payload,
        )
        raw = resp.choices[0].message.content.replace("```json", "").replace("```", "").strip()
        return json.loads(raw)
    except Exception as e:
        print(f"[AI ERROR] {e}")
        return {"message": "Bonjour! Comment puis-je vous aider? 😊", "intent": "inconnu",
                "lead_score": 0, "detected_city": None, "wants_to_order": False,
                "order_description": None, "order_amount": 0, "conversation_summary": ""}


# ═══════════════════════════════════════════════════════
# SERVICE — Green API
# ═══════════════════════════════════════════════════════

def send_whatsapp_message(instance_id: str, token: str, chat_id: str, message: str) -> bool:
    try:
        url = f"https://api.green-api.com/waInstance{instance_id}/sendMessage/{token}"
        resp = requests.post(url, json={"chatId": f"{chat_id}@c.us", "message": message}, timeout=10)
        return resp.status_code == 200
    except Exception as e:
        print(f"[GREEN API ERROR] {e}")
        return False


def send_payment_link_whatsapp(instance_id: str, token: str, chat_id: str,
                                order_number: str, amount: float, url: str, currency: str = "XOF"):
    msg = (f"💳 *Paiement requis*\n\n"
           f"Commande: #{order_number}\n"
           f"Montant: {int(amount):,} {currency}\n\n"
           f"👇 Payez en toute sécurité:\n{url}\n\n"
           f"_Lien valable 24h. Confirmation automatique._")
    send_whatsapp_message(instance_id, token, chat_id, msg)


def send_payment_confirmation_whatsapp(instance_id: str, token: str, chat_id: str,
                                        order_ref: str, amount: float, currency: str = "XOF"):
    msg = (f"✅ *Paiement confirmé!*\n\n"
           f"Commande #{order_ref}\n"
           f"Montant: {int(amount):,} {currency}\n\n"
           f"Merci pour votre achat! 🎉 Votre commande est en cours de traitement.")
    send_whatsapp_message(instance_id, token, chat_id, msg)


# ═══════════════════════════════════════════════════════
# SERVICE — Paiements Mobile Money
# ═══════════════════════════════════════════════════════

def create_cinetpay_link(api_key: str, site_id: str, order_id: str,
                          amount: float, currency: str, description: str) -> dict:
    try:
        resp = requests.post("https://api-checkout.cinetpay.com/v2/payment", json={
            "apikey": api_key, "site_id": site_id,
            "transaction_id": order_id, "amount": int(amount),
            "currency": currency, "description": description,
            "return_url": f"{FRONTEND_URL}/payment/success",
            "notify_url": f"{API_URL}/api/payment/webhook/cinetpay",
            "channels": "ALL", "lang": "fr",
        }, timeout=15)
        data = resp.json()
        if data.get("code") == "201":
            return {"success": True, "url": data["data"]["payment_url"], "token": data["data"]["payment_token"]}
