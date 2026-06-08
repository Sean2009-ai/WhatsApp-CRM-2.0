"""
WhatsApp CRM Lite - app.py
Flask backend: auth, webhook Green API, agent Grok IA, paiements Mobile Money
"""
import os, json, uuid, threading
from datetime import datetime, timedelta
from typing import Optional
from functools import wraps

import requests
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
from flask_jwt_extended import JWTManager, create_access_token, jwt_required, get_jwt_identity
from supabase import create_client, Client
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()

# ── INIT ──────────────────────────────────────────────
app = Flask(__name__, template_folder=".")
CORS(app, origins="*")
app.config["JWT_SECRET_KEY"] = os.getenv("JWT_SECRET", "change-me-in-prod")
app.config["JWT_ACCESS_TOKEN_EXPIRES"] = timedelta(days=7)
jwt = JWTManager(app)

def get_supabase() -> Client:
    url = os.getenv("SUPABASE_URL", "")
    key = os.getenv("SUPABASE_SERVICE_KEY", "")
    if not url or not key:
        raise Exception("Variables Supabase manquantes")
    return create_client(url, key)
def get_supabase() -> Client:
    url = os.environ.get("SUPABASE_URL") or os.getenv("SUPABASE_URL", "")
    key = os.environ.get("SUPABASE_SERVICE_KEY") or os.getenv("SUPABASE_SERVICE_KEY", "")
    print(f"[DEBUG] SUPABASE_URL: {url[:30] if url else 'VIDE'}")
    print(f"[DEBUG] SUPABASE_KEY: {key[:20] if key else 'VIDE'}")
    if not url or not key:
        raise Exception("Variables Supabase manquantes")
    return create_client(url, key)    return decorated


def get_profile(user_id: str) -> Optional[dict]:
    try:
        res = get_supabase().table("profiles").select("*").eq("id", user_id).single().execute()
        return res.data
    except Exception:
        return None


def make_order_number() -> str:
    return "ORD-" + str(int(datetime.utcnow().timestamp())) + "-" + uuid.uuid4().hex[:4].upper()


# ── SERVE HTML ────────────────────────────────────────
@app.route("/")
@app.route("/onboarding")
def index():
    return send_from_directory(".", "onboarding.html")


@app.route("/health")
def health():
    return jsonify({"status": "ok", "time": datetime.utcnow().isoformat()})


# ── AUTH ──────────────────────────────────────────────
@app.route("/api/auth/register", methods=["POST"])
def register():
    data = request.json or {}
    required = ["email", "password", "shop_name", "shop_type", "phone", "country"]
    if not all(data.get(k) for k in required):
        return jsonify({"error": "Champs manquants"}), 400
    try:
        auth_res = get_supabase().auth.sign_up({
            "email": data["email"],
            "password": data["password"]
        })
        uid = auth_res.user.id
        get_supabase().table("profiles").insert({
            "id": uid,
            "email": data["email"],
            "shop_name": data["shop_name"],
            "shop_type": data["shop_type"],
            "phone": data["phone"],
            "country": data["country"],
            "currency": data.get("currency", "XOF"),
            "plan": "trial",
            "is_active": True
        }).execute()
        token = create_access_token(identity=uid)
        return jsonify({"token": token, "user_id": uid}), 201
    except Exception as e:
        return jsonify({"error": str(e)}), 400


@app.route("/api/auth/login", methods=["POST"])
def login():
    data = request.json or {}
    try:
        auth_res = get_supabase().auth.sign_in_with_password({
            "email": data["email"],
            "password": data["password"]
        })
        uid = auth_res.user.id
        profile = get_profile(uid)
        if not profile:
            return jsonify({"error": "Profil introuvable"}), 404
        if not profile.get("is_active"):
            return jsonify({"error": "Compte suspendu"}), 403
        token = create_access_token(identity=uid)
        return jsonify({"token": token, "profile": profile})
    except Exception:
        return jsonify({"error": "Email ou mot de passe incorrect"}), 401


# ── SHOP PROFILE ──────────────────────────────────────
@app.route("/api/shop/profile", methods=["GET"])
@jwt_required()
def shop_profile_get():
    uid = get_jwt_identity()
    profile = get_profile(uid)
    if not profile:
        return jsonify({"error": "Introuvable"}), 404
    profile.pop("payment_api_key", None)
    profile.pop("green_api_token", None)
    return jsonify(profile)


@app.route("/api/shop/profile", methods=["PATCH"])
@jwt_required()
def shop_profile_update():
    uid = get_jwt_identity()
    allowed = [
        "shop_name", "shop_type", "phone", "currency",
        "green_api_instance_id", "green_api_token",
        "payment_provider", "payment_api_key", "payment_site_id",
        "whatsapp_number"
    ]
    data = {k: v for k, v in (request.json or {}).items() if k in allowed}
    get_supabase().table("profiles").update(data).eq("id", uid).execute()
    return jsonify({"success": True})


# ── CATALOG ───────────────────────────────────────────
@app.route("/api/shop/catalog", methods=["GET"])
@jwt_required()
def catalog_get():
    uid = get_jwt_identity()
    res = get_supabase().table("ai_catalog").select("*").eq("profile_id", uid).execute()
    return jsonify(res.data)


@app.route("/api/shop/catalog", methods=["POST"])
@jwt_required()
def catalog_add():
    uid = get_jwt_identity()
    d = request.json or {}
    res = get_supabase().table("ai_catalog").insert({
        "profile_id": uid,
        "name": d.get("name"),
        "description": d.get("description"),
        "price": d.get("price"),
        "category": d.get("category"),
        "availability": d.get("availability", "available"),
        "keywords": d.get("keywords", [])
    }).execute()
    return jsonify(res.data[0]), 201


@app.route("/api/shop/catalog/<item_id>", methods=["DELETE"])
@jwt_required()
def catalog_delete(item_id):
    uid = get_jwt_identity()
    get_supabase().table("ai_catalog").delete().eq("id", item_id).eq("profile_id", uid).execute()
    return jsonify({"success": True})


# ── DASHBOARD ─────────────────────────────────────────
@app.route("/api/shop/dashboard", methods=["GET"])
@jwt_required()
def shop_dashboard():
    uid = get_jwt_identity()
    orders = get_supabase().table("orders").select("*").eq("profile_id", uid).execute().data or []
    contacts = get_supabase().table("contacts").select("*").eq("profile_id", uid).execute().data or []
    paid = [o for o in orders if o["payment_status"] == "paid"]
    pending = [o for o in orders if o["payment_status"] == "pending"]
    total_revenue = sum(float(o["amount"]) for o in paid)
    from collections import defaultdict
    daily = defaultdict(float)
    for o in paid:
        daily[o["created_at"][:10]] += float(o["amount"])
    revenue_chart = [{"date": k, "amount": v} for k, v in sorted(daily.items())[-30:]]
    locations = [
        {"city": c.get("city"), "country": c.get("country"),
         "name": c.get("display_name", c["whatsapp_number"])}
        for c in contacts if c.get("city")
    ]
    recent = sorted(orders, key=lambda x: x["created_at"], reverse=True)[:10]
    return jsonify({
        "stats": {
            "total_revenue": total_revenue,
            "total_orders": len(orders),
            "paid_orders": len(paid),
            "pending_orders": len(pending),
            "total_contacts": len(contacts),
            "conversion_rate": round(len(paid) / max(len(contacts), 1) * 100, 1)
        },
        "revenue_chart": revenue_chart,
        "recent_orders": recent,
        "client_locations": locations
    })


@app.route("/api/shop/orders", methods=["GET"])
@jwt_required()
def orders_get():
    uid = get_jwt_identity()
    status = request.args.get("status")
    q = get_supabase().table("orders").select("*, contacts(display_name,whatsapp_number)").eq("profile_id", uid)
    if status:
        q = q.eq("payment_status", status)
    res = q.order("created_at", desc=True).limit(50).execute()
    return jsonify(res.data)


@app.route("/api/shop/contacts", methods=["GET"])
@jwt_required()
def contacts_get():
    uid = get_jwt_identity()
    res = get_supabase().table("contacts").select("*").eq("profile_id", uid).order("last_contact_at", desc=True).limit(100).execute()
    return jsonify(res.data)


# ── WEBHOOK GREEN API ─────────────────────────────────
@app.route("/webhook/green-api/<instance_id>", methods=["POST"])
def green_webhook(instance_id):
    body = request.json or {}
    t = threading.Thread(target=handle_whatsapp, args=(instance_id, body))
    t.daemon = True
    t.start()
    return jsonify({"received": True}), 200


def handle_whatsapp(instance_id: str, body: dict):
    try:
        if body.get("typeWebhook") != "incomingMessageReceived":
            return
        res = get_supabase().table("profiles").select("*").eq("green_api_instance_id", instance_id).eq("is_active", True).execute()
        if not res.data:
            return
        profile = res.data[0]
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
        # Contact
        cr = get_supabase().table("contacts").select("*").eq("profile_id", profile["id"]).eq("whatsapp_number", chat_id).execute()
        if cr.data:
            contact = cr.data[0]
        else:
            contact = get_supabase().table("contacts").insert({
                "profile_id": profile["id"],
                "whatsapp_number": chat_id,
                "display_name": sender_name
            }).execute().data[0]
        # Conversation
        cvr = get_supabase().table("conversations").select("*").eq("profile_id", profile["id"]).eq("contact_id", contact["id"]).eq("status", "open").execute()
        if cvr.data:
            conv = cvr.data[0]
        else:
            conv = get_supabase().table("conversations").insert({
                "profile_id": profile["id"],
                "contact_id": contact["id"]
            }).execute().data[0]
        # Save inbound
        get_supabase().table("messages").insert({
            "conversation_id": conv["id"],
            "profile_id": profile["id"],
            "direction": "inbound",
            "sender": "client",
            "content": text
        }).execute()
        # Catalog
        cat = get_supabase().table("ai_catalog").select("*").eq("profile_id", profile["id"]).eq("availability", "available").execute().data or []
        # AI
        history = conv.get("ai_conversation_context") or []
        ai = call_grok(profile, contact, text, history, cat)
        # Update contact
        upd = {
            "last_contact_at": datetime.utcnow().isoformat(),
            "lead_score": ai.get("lead_score", 0),
            "intent": ai.get("intent", "inconnu"),
            "last_message_summary": ai.get("conversation_summary", "")
        }
        if ai.get("detected_city"):
            upd["city"] = ai["detected_city"]
        get_supabase().table("contacts").update(upd).eq("id", contact["id"]).execute()
        # Update conversation context
        new_hist = history + [
            {"role": "user", "content": text},
            {"role": "assistant", "content": ai["message"]}
        ]
        get_supabase().table("conversations").update({
            "ai_conversation_context": new_hist[-20:],
            "updated_at": datetime.utcnow().isoformat()
        }).eq("id", conv["id"]).execute()
        # Send reply
        wa_send(profile["green_api_instance_id"], profile["green_api_token"], chat_id, ai["message"])
        # Save outbound
        get_supabase().table("messages").insert({
            "conversation_id": conv["id"],
            "profile_id": profile["id"],
            "direction": "outbound",
            "sender": "ai",
            "content": ai["message"]
        }).execute()
        # Order?
        if ai.get("wants_to_order") and float(ai.get("order_amount", 0)) > 0:
            create_order(profile, contact, conv, ai, chat_id)
    except Exception as e:
        print(f"[WEBHOOK ERROR] {e}")


# ── GROK AI AGENT ─────────────────────────────────────
def call_grok(profile: dict, contact: dict, message: str, history: list, catalog: list) -> dict:
    cat_txt = ""
    if catalog:
        lines = [f"- {p['name']}: {int(p['price']):,} {profile.get('currency','XOF')} - {p.get('description','')}" for p in catalog]
        cat_txt = "\nPRODUITS:\n" + "\n".join(lines)

    system = (
        f"Tu es l'assistant commercial IA de \"{profile['shop_name']}\" sur WhatsApp. "
        f"Qualifie, convainc et vends. Sois chaleureux et concis (max 3 phrases). "
        f"Detecte naturellement la ville du client.{cat_txt}\n"
        f"Client: {contact.get('display_name') or contact['whatsapp_number']}\n"
        f"Reponds UNIQUEMENT en JSON valide: "
        f"{{\"message\":\"reponse\",\"intent\":\"achat|info|support|spam|inconnu\","
        f"\"lead_score\":0,\"detected_city\":null,\"wants_to_order\":false,"
        f"\"order_description\":null,\"order_amount\":0,\"conversation_summary\":\"resume\"}}"
    )

    msgs = [{"role": "system", "content": system}]
    for h in (history or [])[-10:]:
        msgs.append(h)
    msgs.append({"role": "user", "content": message})

    try:
        resp = get_grok().chat.completions.create(
            model="grok-3",
            max_tokens=600,
            messages=msgs
        )
        raw = resp.choices[0].message.content
        raw = raw.replace("```json", "").replace("```", "").strip()
        return json.loads(raw)
    except Exception as e:
        print(f"[GROK ERROR] {e}")
        return {
            "message": "Bonjour! Comment puis-je vous aider? 😊",
            "intent": "inconnu", "lead_score": 0, "detected_city": None,
            "wants_to_order": False, "order_description": None,
            "order_amount": 0, "conversation_summary": ""
        }


# ── GREEN API SENDER ──────────────────────────────────
def wa_send(instance_id: str, token: str, chat_id: str, message: str) -> bool:
    try:
        url = f"https://api.green-api.com/waInstance{instance_id}/sendMessage/{token}"
        r = requests.post(url, json={"chatId": f"{chat_id}@c.us", "message": message}, timeout=10)
        return r.status_code == 200
    except Exception as e:
        print(f"[WA ERROR] {e}")
        return False


# ── PAYMENT ───────────────────────────────────────────
def create_payment_link(provider: str, profile: dict, order_id: str, amount: float, description: str) -> dict:
    currency = profile.get("currency", "XOF")
    api_key = profile.get("payment_api_key", "")
    site_id = profile.get("payment_site_id", "")
    notify = f"{API_URL}/api/payment/webhook/{provider}"
    ret = f"{FRONTEND_URL}/payment/success"

    if provider == "cinetpay":
        try:
            r = requests.post("https://api-checkout.cinetpay.com/v2/payment", json={
                "apikey": api_key, "site_id": site_id,
                "transaction_id": order_id, "amount": int(amount),
                "currency": currency, "description": description,
                "return_url": ret, "notify_url": notify,
                "channels": "ALL", "lang": "fr"
            }, timeout=15)
            d = r.json()
            if d.get("code") == "201":
                return {"success": True, "url": d["data"]["payment_url"]}
            return {"success": False, "error": d.get("message")}
        except Exception as e:
            return {"success": False, "error": str(e)}

    elif provider == "fedapay":
        try:
            headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
            r = requests.post("https://api.fedapay.com/v1/transactions", json={
                "description": description, "amount": int(amount),
                "currency": {"iso": currency}, "callback_url": notify,
                "customer": {"email": f"{order_id}@crm.app"}
            }, headers=headers, timeout=15)
            txn = r.json().get("v1", {}).get("transaction", {})
            if not txn.get("id"):
                return {"success": False, "error": "FedaPay error"}
            tr = requests.post(f"https://api.fedapay.com/v1/transactions/{txn['id']}/token", headers=headers, timeout=15)
            token = tr.json().get("token")
            return {"success": True, "url": f"https://checkout.fedapay.com/{token}"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    elif provider == "hub2":
        try:
            r = requests.post("https://api.hub2.io/v1/payment-intents", json={
                "amount": int(amount), "currency": currency,
                "description": description, "reference": order_id,
                "webhook_url": notify
            }, headers={"Authorization": f"Bearer {api_key}"}, timeout=15)
            d = r.json()
            return {"success": True, "url": d["payment_url"]}
        except Exception as e:
            return {"success": False, "error": str(e)}

    return {"success": False, "error": "Provider inconnu"}


def create_order(profile: dict, contact: dict, conv: dict, ai: dict, chat_id: str):
    ref = make_order_number()
    amount = float(ai["order_amount"])
    desc = ai.get("order_description", "Commande")
    order = get_supabase().table("orders").insert({
        "profile_id": profile["id"],
        "contact_id": contact["id"],
        "conversation_id": conv["id"],
        "order_number": ref,
        "description": desc,
        "amount": amount,
        "currency": profile.get("currency", "XOF"),
        "payment_provider": profile.get("payment_provider", "cinetpay"),
        "payment_reference": ref,
        "payment_status": "pending"
    }).execute().data[0]

    pay = create_payment_link(profile.get("payment_provider", "cinetpay"), profile, ref, amount, desc)
    if pay["success"]:
        get_supabase().table("orders").update({"payment_link": pay["url"]}).eq("id", order["id"]).execute()
        get_supabase().table("conversations").update({"status": "waiting_payment"}).eq("id", conv["id"]).execute()
        msg = (
            f"Commande: #{ref}\n"
            f"Montant: {int(amount):,} {profile.get('currency','XOF')}\n\n"
            f"Payez ici:\n{pay['url']}\n\n"
            f"_Confirmation automatique apres paiement._"
        )
        wa_send(profile["green_api_instance_id"], profile["green_api_token"], chat_id, msg)


def confirm_payment(reference: str):
    try:
        res = get_supabase().table("orders").select("*, profiles!inner(*), contacts(whatsapp_number)").eq("payment_reference", reference).execute()
        if not res.data:
            return
        order = res.data[0]
        if order["payment_status"] == "paid":
            return
        get_supabase().table("orders").update({
            "payment_status": "paid",
            "payment_confirmed_at": datetime.utcnow().isoformat()
        }).eq("id", order["id"]).execute()
        if order.get("conversation_id"):
            get_supabase().table("conversations").update({"status": "completed"}).eq("id", order["conversation_id"]).execute()
        p = order["profiles"]
        c = order.get("contacts", {})
        if p.get("green_api_instance_id") and c.get("whatsapp_number"):
            wa_send(
                p["green_api_instance_id"], p["green_api_token"], c["whatsapp_number"],
                f"Paiement confirme! Commande #{order['order_number']} - {int(float(order['amount'])):,} {order.get('currency','XOF')}. Merci!"
            )
    except Exception as e:
        print(f"[PAYMENT CONFIRM ERROR] {e}")


# ── PAYMENT WEBHOOKS ──────────────────────────────────
@app.route("/api/payment/webhook/cinetpay", methods=["POST"])
def webhook_cinetpay():
    d = request.json or {}
    if d.get("status") == "ACCEPTED":
        confirm_payment(d.get("transaction_id", ""))
    return "OK", 200


@app.route("/api/payment/webhook/fedapay", methods=["POST"])
def webhook_fedapay():
    d = request.json or {}
    if d.get("name") == "transaction.approved":
        ref = (d.get("entity") or {}).get("reference", "")
        confirm_payment(ref)
    return jsonify({"ok": True})


@app.route("/api/payment/webhook/hub2", methods=["POST"])
def webhook_hub2():
    d = request.json or {}
    if d.get("status") == "succeeded":
        confirm_payment(d.get("reference", ""))
    return jsonify({"ok": True})


# ── ADMIN ─────────────────────────────────────────────
@app.route("/api/admin/stats", methods=["GET"])
@admin_required
def admin_stats():
    shops = get_supabase().table("profiles").select("id,is_active,plan").execute().data or []
    orders = get_supabase().table("orders").select("amount,payment_status").execute().data or []
    contacts = get_supabase().table("contacts").select("id").execute().data or []
    paid = [o for o in orders if o["payment_status"] == "paid"]
    return jsonify({
        "total_shops": len(shops),
        "active_shops": sum(1 for s in shops if s["is_active"]),
        "trial_shops": sum(1 for s in shops if s["plan"] == "trial"),
        "total_revenue": sum(float(o["amount"]) for o in paid),
        "total_orders": len(orders),
        "total_contacts": len(contacts)
    })


@app.route("/api/admin/shops", methods=["GET"])
@admin_required
def admin_shops():
    res = get_supabase().table("profiles").select(
        "id,shop_name,shop_type,email,phone,country,whatsapp_number,payment_provider,plan,is_active,green_api_instance_id,created_at"
    ).order("created_at", desc=True).execute()
    return jsonify({"shops": res.data})


@app.route("/api/admin/shops/<shop_id>/toggle", methods=["PATCH"])
@admin_required
def admin_toggle(shop_id):
    res = get_supabase().table("profiles").select("is_active,shop_name").eq("id", shop_id).single().execute()
    if not res.data:
        return jsonify({"error": "Introuvable"}), 404
    new_status = not res.data["is_active"]
    get_supabase().table("profiles").update({"is_active": new_status}).eq("id", shop_id).execute()
    get_supabase().table("admin_logs").insert({
        "admin_email": request.headers.get("X-Admin-Email", "admin"),
        "action": "BLOCK_SHOP" if not new_status else "UNBLOCK_SHOP",
        "target_profile_id": shop_id,
        "details": {"shop_name": res.data["shop_name"]}
    }).execute()
    return jsonify({"success": True, "is_active": new_status})


@app.route("/api/admin/shops/<shop_id>", methods=["DELETE"])
@admin_required
def admin_delete(shop_id):
    get_supabase().table("profiles").delete().eq("id", shop_id).execute()
    return jsonify({"success": True})


@app.route("/api/admin/logs", methods=["GET"])
@admin_required
def admin_logs():
    res = get_supabase().table("admin_logs").select("*").order("created_at", desc=True).limit(100).execute()
    return jsonify(res.data)


# ── RUN ───────────────────────────────────────────────
if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
