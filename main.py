import datetime
import json
import os
import zoneinfo

from calendar_service import (
    cancel_calendar_event,
    check_calendar_events,
    create_calendar_event,
    find_calendar_event,
    reschedule_calendar_event,
)
from fastapi import BackgroundTasks, FastAPI
from fastapi.responses import HTMLResponse
from groq import Groq
from pydantic import BaseModel
import resend

# --- CONFIGURATION & API KEYS ---
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
RESEND_API_KEY = os.getenv("RESEND_API_KEY")
DOCTOR_NOTIFICATION_EMAIL = os.getenv("DOCTOR_NOTIFICATION_EMAIL", "your_email@gmail.com")

ACTIVE_MODEL = "qwen/qwen3.8-27b"
client = Groq(api_key=GROQ_API_KEY)

# Αρχικοποίηση Resend API Key
resend.api_key = RESEND_API_KEY


# --- UNIVERSAL EMAIL NOTIFICATION FUNCTION ---
def send_doctor_notification(doctor_email: str, event_type: str, details: dict):
    """
    Στέλνει ειδοποίηση email στον γιατρό για Νέο Ραντεβού, Ακύρωση ή Αλλαγή.
    event_type: 'CREATE' | 'CANCEL' | 'RESCHEDULE'
    """
    try:
        if not resend.api_key:
            print("⚠️ RESEND_API_KEY δεν έχει οριστεί στα Environment Variables.")
            return

        if event_type == "CREATE":
            subject = f"🔔 Νέο Ραντεβού: {details.get('summary', 'Ασθενής')}"
            title = "Νέο Προγραμματισμένο Ραντεβού"
            color = "#0056b3"
            body = f"""
                <p><b>👤 Στοιχεία / Αιτία:</b> {details.get('summary', 'Δεν δηλώθηκε')}</p>
                <p><b>📞 Τηλέφωνο Ασθενούς:</b> {details.get('patient_phone', 'Δεν δηλώθηκε')}</p>
                <p><b>📅 Έναρξη (ISO):</b> {details.get('start_iso', 'Δεν δηλώθηκε')}</p>
                <p><b>⏰ Λήξη (ISO):</b> {details.get('end_iso', 'Δεν δηλώθηκε')}</p>
                <p><b>ℹ️ Αποτέλεσμα Calendar:</b> {details.get('tool_result', '')}</p>
            """
        elif event_type == "CANCEL":
            subject = f"❌ Ακύρωση Ραντεβού: {details.get('booking_code_or_phone')}"
            title = "Ακύρωση Ραντεβού"
            color = "#dc3545"
            body = f"""
                <p><b>🆔 Κωδικός / Τηλέφωνο:</b> <code>{details.get('booking_code_or_phone')}</code></p>
                <p><b>ℹ️ Κατάσταση:</b> {details.get('tool_result', '')}</p>
            """
        elif event_type == "RESCHEDULE":
            subject = f"🔄 Αλλαγή Ώρας Ραντεβού: {details.get('booking_code_or_phone')}"
            title = "Μεταφορά / Αλλαγή Ώρας Ραντεβού"
            color = "#d97706"
            body = f"""
                <p><b>🆔 Κωδικός / Τηλέφωνο:</b> <code>{details.get('booking_code_or_phone')}</code></p>
                <p><b>📅 Νέα Έναρξη (ISO):</b> {details.get('new_start_iso')}</p>
                <p><b>⏰ Νέα Λήξη (ISO):</b> {details.get('new_end_iso')}</p>
                <p><b>ℹ️ Κατάσταση:</b> {details.get('tool_result', '')}</p>
            """
        else:
            return

        params = {
            "from": "OffVoice AI <onboarding@resend.dev>",
            "to": [doctor_email],
            "subject": subject,
            "html": f"""
            <div style="font-family: Arial, sans-serif; color: #333; max-width: 600px; padding: 20px; border: 1px solid #e0e0e0; border-radius: 8px;">
                <h2 style="color: {color}; margin-top: 0;">{title}</h2>
                <p>Ο ψηφιακός βοηθός <b>OffVoice AI</b> επεξεργάστηκε μια ενέργεια στο Google Calendar.</p>
                <hr style="border: none; border-top: 1px solid #eee; margin: 15px 0;">
                {body}
                <hr style="border: none; border-top: 1px solid #eee; margin: 15px 0;">
                <p style="font-size: 12px; color: #777;">OffVoice AI System • Αυτόματη ειδοποίηση ιατρείου</p>
            </div>
            """,
        }

        response = resend.Emails.send(params)
        print(f"✅ Email ειδοποίησης ({event_type}) στάλθηκε στο {doctor_email}. ID: {response.get('id')}")

    except Exception as e:
        print(f"❌ Σφάλμα κατά την αποστολή email μέσω Resend: {e}")


tools = [
    {
        "type": "function",
        "function": {
            "name": "find_calendar_event",
            "description": "Αναζητά τα στοιχεία ενός ραντεβού με βάση τον 6ψήφιο κωδικό ή τηλέφωνο.",
            "parameters": {
                "type": "object",
                "properties": {"booking_code_or_phone": {"type": "string"}},
                "required": ["booking_code_or_phone"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_calendar_event",
            "description": "Καταχωρεί νέο ραντεβού.",
            "parameters": {
                "type": "object",
                "properties": {
                    "summary": {"type": "string"},
                    "patient_phone": {"type": "string"},
                    "start_iso": {"type": "string"},
                    "end_iso": {"type": "string"},
                },
                "required": ["summary", "patient_phone", "start_iso", "end_iso"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "cancel_calendar_event",
            "description": "Διαγράφει οριστικά ένα ραντεβού.",
            "parameters": {
                "type": "object",
                "properties": {"booking_code_or_phone": {"type": "string"}},
                "required": ["booking_code_or_phone"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "reschedule_calendar_event",
            "description": "Μεταφέρει ραντεβού σε νέα ώρα.",
            "parameters": {
                "type": "object",
                "properties": {
                    "booking_code_or_phone": {"type": "string"},
                    "new_start_iso": {"type": "string"},
                    "new_end_iso": {"type": "string"},
                },
                "required": ["booking_code_or_phone", "new_start_iso", "new_end_iso"],
            },
        },
    },
]


def get_system_prompt():
    today_dt = datetime.datetime.now(zoneinfo.ZoneInfo("Europe/Athens"))
    days_gr = ["Δευτέρα", "Τρίτη", "Τετάρτη", "Πέμπτη", "Παρασκευή", "Σάββατο", "Κυριακή"]

    dates_reference = f"=== ΗΜΕΡΟΛΟΓΙΟ (ΣΗΜΕΡΑ: {days_gr[today_dt.weekday()]} {today_dt.strftime('%d/%m/%Y')}) ===\n"
    for i in range(14):
        day = today_dt + datetime.timedelta(days=i)
        dates_reference += f"- {days_gr[day.weekday()]} {day.strftime('%d/%m/%Y')} -> ISO: {day.strftime('%Y-%m-%d')}\n"

    return f"""
Είσαι η ψηφιακή βοηθός (AI Receptionist) του Δοκιμαστικού Ιατρείου (Δρ. TEST - Γυναικολόγος / Μαιευτήρας).

{dates_reference}

=== ΚΑΝΟΝΕΣ ΕΠΙΚΟΙΝΩΝΙΑΣ (ΑΥΣΤΗΡΟ) ===
1. ΣΤΥΛ: Απαντάς ΠΑΝΤΑ σύντομα, επαγγελματικά και ευγενικά (1-2 προτάσεις το πολύ).
2. ΣΥΛΛΟΓΗ ΣΤΟΙΧΕΙΩΝ (ΑΜΕΣΗ & ΟΙΚΟΝΟΜΙΚΗ):
   - Όταν ο χρήστης θέλει να κλείσει ραντεβού και δεν έχει δώσει όλα τα στοιχεία, ΖΗΤΑ ΤΑ ΟΛΑ ΜΑΖΙ ΣΕ ΕΝΑ ΜΗΝΥΜΑ:
     "Παρακαλώ σημειώστε μου: 1) Ονοματεπώνυμο, 2) Τηλέφωνο, 3) Επιθυμητή ημερομηνία/ώρα και 4) Αιτία επίσκεψης."
   - ΑΠΑΓΟΡΕΥΕΤΑΙ να ζητάς τα στοιχεία ένα-ένα σε ξεχωριστά μηνύματα.
3. ΑΠΑΓΟΡΕΥΣΗ ΠΕΡΙΤΤΩΝ ΠΛΗΡΟΦΟΡΙΩΝ:
   - ΜΗΝ εξηγείς ΠΟΤΕ στον χρήστη τεχνικούς κανόνες (π.χ. για 30 λεπτά, για συγκρούσεις ραντεβού, ή για το τι είναι διαθέσιμο).
   - ΜΗΝ αναφέρεις ΠΟΤΕ ποιες ώρες είναι κατειλημμένες.
   - Για αλλαγή ραντεβού, μόλις βρεθεί ο κωδικός, ρώτα ΑΠΛΑ: "Ποια νέα ημερομηνία και ώρα επιθυμείτε;"
4. GDPR: Απαγορεύεται η αποκάλυψη στοιχείων άλλων ασθενών.
5. OUT-OF-SCOPE: Αρνήσου ευγενικά ερωτήσεις εκτός ιατρείου.
6. ΛΕΙΤΟΥΡΓΙΑ 🏥: Δευτέρα έως Παρασκευή 09:00 - 17:00 (30 λεπτά ανά ραντεβού).

=== ΛΕΙΤΟΥΡΓΙΕΣ ===
1. ΝΕΟ ΡΑΝΤΕΒΟΥ: Μόλις συγκεντρωθούν και τα 4 στοιχεία (Ονοματεπώνυμο, Τηλέφωνο, Ημερομηνία & Ώρα, Αιτία), καλείς `create_calendar_event`.
2. ΑΛΛΑΓΗ / ΑΚΥΡΩΣΗ: Ζητάς τον κωδικό ή τηλέφωνο, καλείς `find_calendar_event`, και μετά προχωράς σε αλλαγή ή ακύρωση.

=== ΔΙΑΘΕΣΙΜΟΤΗΤΑ ===
{check_calendar_events()}
"""


app = FastAPI()
chat_history = [{"role": "system", "content": get_system_prompt()}]


class ChatRequest(BaseModel):
    message: str


HTML_CONTENT = """
<!DOCTYPE html>
<html lang="el">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Δρ. TEST - AI Assistant</title>
    <style>
        body { font-family: 'Segoe UI', sans-serif; background: #f4f7f6; margin: 0; padding: 10px; display: flex; justify-content: center; }
        .chat-container { width: 100%; max-width: 450px; background: white; border-radius: 12px; box-shadow: 0 4px 15px rgba(0,0,0,0.1); overflow: hidden; display: flex; flex-direction: column; height: 80vh; }
        .chat-header { background: #0056b3; color: white; padding: 15px; text-align: center; font-weight: bold; display: flex; justify-content: space-between; align-items: center; }
        .reset-btn { background: #dc3545; color: white; border: none; padding: 5px 10px; border-radius: 5px; cursor: pointer; font-size: 12px; }
        .chat-messages { flex: 1; padding: 15px; overflow-y: auto; display: flex; flex-direction: column; gap: 10px; }
        .message { max-width: 85%; padding: 10px 14px; border-radius: 15px; font-size: 14px; line-height: 1.4; white-space: pre-wrap; }
        .user-message { background: #0056b3; color: white; align-self: flex-end; border-bottom-right-radius: 2px; }
        .bot-message { background: #e9ecef; color: #333; align-self: flex-start; border-bottom-left-radius: 2px; }
        .chat-input { display: flex; padding: 10px; border-top: 1px solid #ddd; background: #fff; }
        .chat-input input { flex: 1; padding: 10px; border: 1px solid #ccc; border-radius: 20px; outline: none; }
        .chat-input button { background: #0056b3; color: white; border: none; padding: 10px 15px; margin-left: 8px; border-radius: 50%; cursor: pointer; }
    </style>
</head>
<body>
    <div class="chat-container">
        <div class="chat-header">
            <span>Δρ. TEST - AI Assistant (Test Mode)</span>
            <button class="reset-btn" onclick="resetChat()">Επαναφορά</button>
        </div>
        <div class="chat-messages" id="messages">
            <div class="message bot-message">ℹ️ <b>Ενημέρωση GDPR</b>: Με τη συνέχιση της συνομιλίας, αποδέχεστε τη συλλογή και επεξεργασία των βασικών στοιχείων σας αποκλειστικά για τον προγραμματισμό του ραντεβού σας.<br><br>Γεια σας! Είμαι η ψηφιακή βοηθός του Δοκιμαστικού Ιατρείου (Δρ. TEST). Πώς μπορώ να σας εξυπηρετήσω;</div>
        </div>
        <div class="chat-input">
            <input type="text" id="userInput" placeholder="Γράψτε το μήνυμά σας..." onkeypress="handleKeyPress(event)">
            <button onclick="sendMessage()">➤</button>
        </div>
    </div>
    <script>
        async function sendMessage() {
            const input = document.getElementById('userInput');
            const message = input.value.trim();
            if (!message) return;
            appendMessage(message, 'user-message');
            input.value = '';
            try {
                const response = await fetch('/chat', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ message: message })
                });
                const data = await response.json();
                appendMessage(data.response, 'bot-message');
            } catch (error) {
                appendMessage('Σφάλμα επικοινωνίας.', 'bot-message');
            }
        }
        async function resetChat() {
            await fetch('/reset', { method: 'POST' });
            document.getElementById('messages').innerHTML = `
                <div class="message bot-message">ℹ️ <b>Ενημέρωση GDPR</b>: Με τη συνέχιση της συνομιλίας, αποδέχεστε τη συλλογή και επεξεργασία των βασικών στοιχείων σας αποκλειστικά για τον προγραμματισμό του ραντεβού σας.<br><br>Γεια σας! Είμαι η ψηφιακή βοηθός του Δοκιμαστικού Ιατρείου (Δρ. TEST). Πώς μπορώ να σας εξυπηρετήσω;</div>
            `;
        }
        function appendMessage(text, className) {
            const messagesDiv = document.getElementById('messages');
            const msg = document.createElement('div');
            msg.className = `message ${className}`;
            msg.innerText = text;
            messagesDiv.appendChild(msg);
            messagesDiv.scrollTop = messagesDiv.scrollHeight;
        }
        function handleKeyPress(e) { if (e.key === 'Enter') sendMessage(); }
    </script>
</body>
</html>
"""


@app.get("/", response_class=HTMLResponse)
def get_webpage():
    return HTML_CONTENT


@app.post("/chat")
async def chat_endpoint(request: ChatRequest, background_tasks: BackgroundTasks):
    global chat_history
    try:
        chat_history[0] = {"role": "system", "content": get_system_prompt()}
        chat_history.append({"role": "user", "content": request.message})

        completion = client.chat.completions.create(
            model=ACTIVE_MODEL,
            messages=chat_history,
            tools=tools,
            tool_choice="auto",
            temperature=0.0,
        )
        response_message = completion.choices[0].message

        if response_message.tool_calls:
            for tool_call in response_message.tool_calls:
                func_name = tool_call.function.name
                args = json.loads(tool_call.function.arguments)
                tool_result = ""

                if func_name == "find_calendar_event":
                    tool_result = find_calendar_event(args.get("booking_code_or_phone"))

                elif func_name == "create_calendar_event":
                    tool_result = create_calendar_event(
                        args.get("summary"),
                        args.get("patient_phone"),
                        args.get("start_iso"),
                        args.get("end_iso"),
                    )
                    background_tasks.add_task(
                        send_doctor_notification,
                        doctor_email=DOCTOR_NOTIFICATION_EMAIL,
                        event_type="CREATE",
                        details={
                            "summary": args.get("summary"),
                            "patient_phone": args.get("patient_phone"),
                            "start_iso": args.get("start_iso"),
                            "end_iso": args.get("end_iso"),
                            "tool_result": tool_result,
                        },
                    )

                elif func_name == "cancel_calendar_event":
                    tool_result = cancel_calendar_event(args.get("booking_code_or_phone"))
                    background_tasks.add_task(
                        send_doctor_notification,
                        doctor_email=DOCTOR_NOTIFICATION_EMAIL,
                        event_type="CANCEL",
                        details={
                            "booking_code_or_phone": args.get("booking_code_or_phone"),
                            "tool_result": tool_result,
                        },
                    )

                elif func_name == "reschedule_calendar_event":
                    tool_result = reschedule_calendar_event(
                        args.get("booking_code_or_phone"),
                        args.get("new_start_iso"),
                        args.get("new_end_iso"),
                    )
                    background_tasks.add_task(
                        send_doctor_notification,
                        doctor_email=DOCTOR_NOTIFICATION_EMAIL,
                        event_type="RESCHEDULE",
                        details={
                            "booking_code_or_phone": args.get("booking_code_or_phone"),
                            "new_start_iso": args.get("new_start_iso"),
                            "new_end_iso": args.get("new_end_iso"),
                            "tool_result": tool_result,
                        },
                    )

                chat_history.append(response_message)
                chat_history.append({"role": "tool", "tool_call_id": tool_call.id, "content": tool_result})

                second_completion = client.chat.completions.create(
                    model=ACTIVE_MODEL, messages=chat_history, temperature=0.0
                )
                bot_response = second_completion.choices[0].message.content
                chat_history.append({"role": "assistant", "content": bot_response})
                return {"response": bot_response}

        bot_response = response_message.content
        chat_history.append({"role": "assistant", "content": bot_response})
        return {"response": bot_response}

    except Exception as e:
        return {"response": f"Σφάλμα AI: {str(e)}"}


@app.post("/reset")
async def reset_endpoint():
    global chat_history
    chat_history = [{"role": "system", "content": get_system_prompt()}]
    return {"status": "ok"}
