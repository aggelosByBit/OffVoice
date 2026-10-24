import os
import json
import datetime
import zoneinfo
import resend
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ENV_PATH = os.path.join(BASE_DIR, ".env")

def load_environment():
    """Φορτώνει αυτόματα τις μεταβλητές από το .env αν υπάρχουν."""
    try:
        from dotenv import load_dotenv
        load_dotenv(ENV_PATH)
    except ImportError:
        pass

    if os.path.exists(ENV_PATH):
        with open(ENV_PATH, "r", encoding="utf-8") as f:
            for line in f.read().splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, val = line.split("=", 1)
                key = key.strip()
                val = val.strip().strip('"').strip("'")
                if key not in os.environ or not os.environ[key]:
                    os.environ[key] = val

load_environment()

SCOPES = ["https://www.googleapis.com/auth/calendar.readonly"]

def get_calendar_service():
    json_file_path = os.path.join(BASE_DIR, "credentials.json")
    if os.path.exists(json_file_path):
        creds = Credentials.from_service_account_file(json_file_path, scopes=SCOPES)
        return build("calendar", "v3", credentials=creds)

    creds_json_str = os.getenv("GOOGLE_CREDENTIALS")
    if not creds_json_str:
        raise ValueError("Δεν βρέθηκε ούτε το αρχείο credentials.json ούτε η μεταβλητή GOOGLE_CREDENTIALS.")
    
    creds_json_str = creds_json_str.strip("'\"")
    creds_info = json.loads(creds_json_str)
    
    if "private_key" in creds_info:
        pk = creds_info["private_key"]
        pk = pk.replace("\\\\n", "\n").replace("\\n", "\n")
        lines = [line.strip() for line in pk.splitlines() if line.strip()]
        creds_info["private_key"] = "\n".join(lines) + "\n"

    creds = Credentials.from_service_account_info(creds_info, scopes=SCOPES)
    return build("calendar", "v3", credentials=creds)

def generate_and_send_monthly_report(doctor_email: str = None, doctor_name: str = None):
    if not doctor_email:
        doctor_email = os.getenv("DOCTOR_NOTIFICATION_EMAIL")
    if not doctor_name:
        doctor_name = os.getenv("DOCTOR_NAME", "Γιατρέ")

    if not doctor_email:
        raise ValueError("Δεν βρέθηκε email γιατρού στη μεταβλητή DOCTOR_NOTIFICATION_EMAIL.")

    # Ρυθμίσεις ωραρίου και ημερών ιατρείου από τα env
    open_hour = int(os.getenv("CLINIC_OPEN_HOUR", "8"))
    close_hour = int(os.getenv("CLINIC_CLOSE_HOUR", "20"))
    
    work_days_env = os.getenv("CLINIC_WORK_DAYS", "0,1,2,3,4")
    work_days = [int(d.strip()) for d in work_days_env.split(",") if d.strip().isdigit()]

    day_map = {0: "Δευτέρα", 1: "Τρίτη", 2: "Τετάρτη", 3: "Πέμπτη", 4: "Παρασκευή", 5: "Σάββατο", 6: "Κυριακή"}
    work_days_str = ", ".join([day_map[d] for d in work_days if d in day_map])

    service = get_calendar_service()
    tz = zoneinfo.ZoneInfo("Europe/Athens")
    now = datetime.datetime.now(tz)

    time_max = now.isoformat()
    time_min = (now - datetime.timedelta(days=30)).isoformat()

    events_result = service.events().list(
        calendarId="primary",
        timeMin=time_min,
        timeMax=time_max,
        singleEvents=True,
        showDeleted=True,
        orderBy="startTime"
    ).execute()

    items = events_result.get("items", [])

    total_appointments = 0
    completed_appointments = 0
    cancelled_appointments = 0
    after_hours_count = 0

    for event in items:
        status = event.get("status")
        if status == "cancelled":
            cancelled_appointments += 1
            continue

        # --- ΦΙΛΤΡΟ ΑΣΦΑΛΕΙΑΣ: ΜΟΝΟ ΡΑΝΤΕΒΟΥ ΤΟΥ OFFVOICE AI ---
        description = event.get("description", "")
        if "OffVoice AI" not in description:
            continue  # Αγνοεί οτιδήποτε δεν δημιουργήθηκε από τον ψηφιακό βοηθό!
        # --------------------------------------------------------

        total_appointments += 1

        end_str = event["end"].get("dateTime", event["end"].get("date"))
        try:
            end_dt = datetime.datetime.fromisoformat(end_str)
            if end_dt.tzinfo is None:
                end_dt = end_dt.replace(tzinfo=tz)
            if now > end_dt:
                completed_appointments += 1
        except Exception:
            completed_appointments += 1

        # Διαβάζουμε την ημερομηνία & ώρα ΠΟΥ ΕΓΙΝΕ η κράτηση (created timestamp)
        created_str = event.get("created")
        if created_str:
            try:
                created_dt = datetime.datetime.fromisoformat(created_str.replace("Z", "+00:00")).astimezone(tz)
                
                # Έλεγχος αν η ΚΡΑΤΗΣΗ έγινε σε μέρα ή ώρα που το ιατρείο ήταν κλειστό
                is_non_work_day = created_dt.weekday() not in work_days
                is_outside_hours = created_dt.hour < open_hour or created_dt.hour >= close_hour

                if is_non_work_day or is_outside_hours:
                    after_hours_count += 1
            except Exception:
                pass

    min_minutes = total_appointments * 2
    max_minutes = total_appointments * 3

    if max_minutes >= 60:
        min_hours = round(min_minutes / 60, 1)
        max_hours = round(max_minutes / 60, 1)
        time_saved_str = f"{min_hours} - {max_hours} ώρες"
    else:
        time_saved_str = f"{min_minutes} - {max_minutes} λεπτά"

    html_content = f"""
    <div style="font-family: Arial, sans-serif; color: #333; max-width: 600px; margin: auto; padding: 25px; border: 1px solid #e0e0e0; border-radius: 12px; background-color: #ffffff;">
        <h2 style="color: #1a365d; text-align: center; margin-bottom: 20px;">📊 Μηνιαία Αναφορά Αποδοτικότητας OffVoice AI</h2>
        <p style="font-size: 16px;">Αγαπητέ/ή <strong>{doctor_name}</strong>,</p>
        <p style="font-size: 14px; color: #555;">Ακολουθεί η σύνοψη της δραστηριότητας του ψηφιακού σας βοηθού <strong>OffVoice AI</strong> για τις τελευταίες 30 ημέρες:</p>

        <hr style="border: none; border-top: 1px solid #eee; margin: 20px 0;">

        <h3 style="color: #2b6cb0; font-size: 16px;">📈 Στατιστικά Ραντεβού</h3>
        <ul style="font-size: 15px; line-height: 1.8;">
            <li><strong>Συνολικά Αιτήματα:</strong> {total_appointments} ραντεβού</li>
            <li><strong>Ολοκληρωμένες Επισκέψεις:</strong> <span style="color: #27ae60;">{completed_appointments}</span></li>
            <li><strong>Ακυρώσεις / Αλλαγές:</strong> <span style="color: #c0392b;">{cancelled_appointments}</span></li>
        </ul>

        <h3 style="color: #2b6cb0; font-size: 16px;">⏳ Όφελος Ιατρείου</h3>
        <ul style="font-size: 15px; line-height: 1.8;">
            <li><strong>Εξοικονομημένος Χρόνος:</strong> <strong>{time_saved_str}</strong> καθαρής εργασίας γραμματείας <em style="font-size: 12px; color: #777;">(βάσει 2-3 λεπτών ανά κλήση/ραντεβού)</em></li>
            <li><strong>Κρατήσεις Εκτός Ωραρίου:</strong> <strong>{after_hours_count} ραντεβού</strong> <em style="font-size: 12px; color: #777;">(κλήσεις που πραγματοποιήθηκαν εκτός ωραρίου {open_hour}:00 - {close_hour}:00 ή εκτός ημερών: {work_days_str})</em></li>
        </ul>

        <hr style="border: none; border-top: 1px solid #eee; margin: 20px 0;">

        <p style="font-size: 13px; color: #718096; text-align: center; font-style: italic;">
            💡 Το OffVoice AI διατήρησε τη γραμμή σας ανοιχτή 24/7 (Ωράριο ιατρείου: {open_hour}:00 - {close_hour}:00 | Ημέρες: {work_days_str}) καταγράφοντας ραντεβού ακόμα και όταν το ιατρείο ήταν κλειστό.
        </p>
    </div>
    """

    resend_api_key = os.getenv("RESEND_API_KEY")
    if not resend_api_key:
        raise ValueError("Δεν βρέθηκε η μεταβλητή RESEND_API_KEY.")

    resend.api_key = resend_api_key

    params = {
        "from": "OffVoice AI <onboarding@resend.dev>",
        "to": [doctor_email],
        "subject": f"📊 Μηνιαία Αναφορά Αποδοτικότητας OffVoice AI - {doctor_name}",
        "html": html_content
    }

    response = resend.Emails.send(params)
    print(f"✅ Η αναφορά στάλθηκε επιτυχώς στο {doctor_email}! Resend ID: {response.get('id')}")
    return response

if __name__ == "__main__":
    generate_and_send_monthly_report()
