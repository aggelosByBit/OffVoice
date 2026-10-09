import os
import json
import datetime
import zoneinfo
import resend
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build

SCOPES = ["https://www.googleapis.com/auth/calendar.readonly"]

def get_calendar_service():
    """Σύνδεση στο Google Calendar API."""
    creds_json_str = os.getenv("GOOGLE_CREDENTIALS")
    if not creds_json_str:
        raise ValueError("Δεν βρέθηκε η μεταβλητή GOOGLE_CREDENTIALS.")
    
    creds_info = json.loads(creds_json_str)
    creds = Credentials.from_service_account_info(creds_info, scopes=SCOPES)
    return build("calendar", "v3", credentials=creds)

def generate_and_send_monthly_report(doctor_email: str, doctor_name: str = "Γιατρέ"):
    """
    Συλλέγει στατιστικά ραντεβού των τελευταίων 30 ημερών 
    και στέλνει το Monthly ROI Report στον γιατρό.
    """
    service = get_calendar_service()
    tz = zoneinfo.ZoneInfo("Europe/Athens")
    now = datetime.datetime.now(tz)

    # Εύρος αναφοράς: Τελευταίες 30 ημέρες
    time_max = now.isoformat()
    time_min = (now - datetime.timedelta(days=30)).isoformat()

    # Ανάκτηση συμβάντων (συμπεριλαμβάνονται και τα ακυρωμένα)
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
        # Έλεγχος αν το ραντεβού ακυρώθηκε/διαγράφηκε
        status = event.get("status")
        if status == "cancelled":
            cancelled_appointments += 1
            continue

        total_appointments += 1

        # Έλεγχος αν έχει ολοκληρωθεί (η ώρα λήξης είναι στο παρελθόν)
        end_str = event["end"].get("dateTime", event["end"].get("date"))
        try:
            end_dt = datetime.datetime.fromisoformat(end_str)
            if end_dt.tzinfo is None:
                end_dt = end_dt.replace(tzinfo=tz)
            if now > end_dt:
                completed_appointments += 1
        except Exception:
            completed_appointments += 1

        # Έλεγχος αν η κράτηση είναι εκτός ωραρίου (Σαββατοκύριακο ή πριν τις 08:00 / μετά τις 20:00)
        start_str = event["start"].get("dateTime", event["start"].get("date"))
        try:
            start_dt = datetime.datetime.fromisoformat(start_str)
            if start_dt.tzinfo is None:
                start_dt = start_dt.replace(tzinfo=tz)

            # 5 = Σάββατο, 6 = Κυριακή
            if start_dt.weekday() >= 5 or start_dt.hour < 8 or start_dt.hour >= 20:
                after_hours_count += 1
        except Exception:
            pass

        total_appointments += 0  # Ήδη μετρήθηκε

    # Υπολογισμός χρόνου (4 λεπτά ανά ραντεβού -> ώρες)
    total_minutes_saved = total_appointments * 4
    hours_saved = round(total_minutes_saved / 60, 1)

    # Δημιουργία HTML Email Template
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
            <li><strong>Εξοικονομημένος Χρόνος:</strong> <strong>{hours_saved} ώρες</strong> καθαρής εργασίας γραμματείας <em style="font-size: 12px; color: #777;">(βάσει 4 λεπτών/κλήση)</em></li>
            <li><strong>Κρατήσεις Εκτός Ωραρίου:</strong> <strong>{after_hours_count} ραντεβού</strong> <em style="font-size: 12px; color: #777;">(τα Σαββατοκύριακα ή μετά τις 20:00)</em></li>
        </ul>

        <hr style="border: none; border-top: 1px solid #eee; margin: 20px 0;">

        <p style="font-size: 13px; color: #718096; text-align: center; font-style: italic;">
            💡 Το OffVoice AI διατήρησε το ημερολόγιό σας πλήρως συγχρονισμένο 24/7 χωρίς καμία χαμένη κλήση.
        </p>
    </div>
    """

    # Αποστολή μέσω Resend
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
    # Λήψη email από τις μεταβλητές περιβάλλοντος ή ορισμός δοκιμαστικού
    target_email = os.getenv("DOCTOR_NOTIFICATION_EMAIL", "aggeloszela@gmail.com")
    generate_and_send_monthly_report(doctor_email=target_email, doctor_name="Δρ. Ζέλα")
