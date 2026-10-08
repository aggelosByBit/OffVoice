import os

import json

import random

import string

import datetime

import zoneinfo

from google.oauth2.service_account import Credentials

from googleapiclient.discovery import build



# Ανάκτηση credentials από Environment Variables του Render

CALENDAR_ID = os.getenv("CALENDAR_ID")

creds_json_str = os.getenv("GOOGLE_CREDENTIALS_JSON")



if creds_json_str:

    creds_info = json.loads(creds_json_str)

    scopes = ["https://www.googleapis.com/auth/calendar"]

    credentials = Credentials.from_service_account_info(creds_info, scopes=scopes)

    calendar_service = build("calendar", "v3", credentials=credentials)

else:

    calendar_service = None



def generate_booking_code():

    return ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))



def is_within_working_hours(dt: datetime.datetime) -> tuple[bool, str]:

    if dt.weekday() >= 5:

        return False, "Το ιατρείο είναι κλειστό τα Σαββατοκύριακα."

    if not (datetime.time(9, 0) <= dt.time() < datetime.time(17, 0)):

        return False, "Το ωράριο είναι καθημερινές 09:00 έως 17:00."

    return True, "OK"



def search_event_in_calendar(query_str):

    query = query_str.strip().upper()

    if not query or not calendar_service:

        return None

    try:

        athens_tz = zoneinfo.ZoneInfo("Europe/Athens")

        now_dt = datetime.datetime.now(athens_tz)

        start_search = (now_dt - datetime.timedelta(days=1)).replace(hour=0, minute=0, second=0).isoformat()



        events_result = calendar_service.events().list(

            calendarId=CALENDAR_ID,

            timeMin=start_search,

            maxResults=250,

            singleEvents=True,

            orderBy='startTime'

        ).execute()

        

        for event in events_result.get('items', []):

            if event.get('status') == 'cancelled':

                continue

            desc = str(event.get('description', '')).upper()

            summary = str(event.get('summary', '')).upper()

            if query in desc or query in summary:

                return event

    except Exception as e:

        print(f"❌ [SEARCH ERROR] {str(e)}")

    return None



def check_calendar_events():

    if not calendar_service:

        return "Υπηρεσία ημερολογίου μη διαθέσιμη."

    try:

        now = datetime.datetime.now(zoneinfo.ZoneInfo("Europe/Athens")).isoformat()

        events_result = calendar_service.events().list(

            calendarId=CALENDAR_ID, timeMin=now,

            maxResults=50, singleEvents=True,

            orderBy='startTime'

        ).execute()

        events = events_result.get('items', [])

        if not events:

            return "Όλες οι ώρες είναι διαθέσιμες."

        

        summary = "Κατειλημμένες ώρες στο ημερολόγιο:\n"

        for event in events:

            if event.get('status') == 'cancelled':

                continue

            start_str = event['start'].get('dateTime', event['start'].get('date'))

            end_str = event['end'].get('dateTime', event['end'].get('date'))

            if start_str:

                dt_s = datetime.datetime.fromisoformat(start_str)

                dt_e = datetime.datetime.fromisoformat(end_str) if end_str else dt_s + datetime.timedelta(minutes=30)

                summary += f"- Κατειλημμένη ώρα: {dt_s.strftime('%d/%m/%Y %H:%M')} έως {dt_e.strftime('%H:%M')}\n"

        return summary

    except Exception as e:

        return f"Σφάλμα ανάγνωσης: {str(e)}"



def find_calendar_event(booking_code_or_phone):

    target_event = search_event_in_calendar(booking_code_or_phone)

    if not target_event:

        return f"ERROR_NOT_FOUND: ΔΕΝ βρέθηκε ραντεβού με τον κωδικό/τηλέφωνο '{booking_code_or_phone}'."



    summary_text = target_event.get('summary', 'Ραντεβού')

    start_dt_str = target_event['start'].get('dateTime', '')

    desc_text = target_event.get('description', '')



    if start_dt_str:

        dt_obj = datetime.datetime.fromisoformat(start_dt_str)

        days_gr = ["Δευτέρα", "Τρίτη", "Τετάρτη", "Πέμπτη", "Παρασκευή", "Σάββατο", "Κυριακή"]

        day_name = days_gr[dt_obj.weekday()]

        formatted_date = f"{day_name} {dt_obj.strftime('%d/%m/%Y')} στις {dt_obj.strftime('%H:%M')}"

    else:

        formatted_date = "άγνωστη ημερομηνία"



    return f"REAL_DATA_FOUND: Τίτλος: '{summary_text}', Ημερομηνία/Ώρα: '{formatted_date}', Στοιχεία: '{desc_text}'."



def create_calendar_event(summary, patient_phone, start_iso, end_iso):

    try:

        start_iso = start_iso.replace(" ", "T")

        end_iso = end_iso.replace(" ", "T")

        if len(start_iso) == 16: start_iso += ":00"

        if len(end_iso) == 16: end_iso += ":00"



        athens_tz = zoneinfo.ZoneInfo("Europe/Athens")

        dt_start = datetime.datetime.fromisoformat(start_iso).replace(tzinfo=athens_tz)

        dt_end = datetime.datetime.fromisoformat(end_iso).replace(tzinfo=athens_tz)



        is_valid, msg = is_within_working_hours(dt_start)

        if not is_valid:

            return f"ERROR_OUT_OF_HOURS: {msg}"



        events_result = calendar_service.events().list(

            calendarId=CALENDAR_ID, timeMin=dt_start.isoformat(),

            timeMax=dt_end.isoformat(), singleEvents=True

        ).execute()

        if [e for e in events_result.get('items', []) if e.get('status') != 'cancelled']:

            return "ERROR_SLOT_TAKEN: Η συγκεκριμένη ώρα είναι ήδη κατειλημμένη!"



        booking_code = generate_booking_code()

        event = {

            'summary': summary,

            'description': f"Κωδικός Κράτησης: {booking_code}\nΤηλέφωνο ασθενούς: {patient_phone}",

            'start': {'dateTime': dt_start.isoformat(), 'timeZone': 'Europe/Athens'},

            'end': {'dateTime': dt_end.isoformat(), 'timeZone': 'Europe/Athens'},

        }

        calendar_service.events().insert(calendarId=CALENDAR_ID, body=event).execute()

        return f"SUCCESS: Το ραντεβού καταχωρήθηκε! Ο 6ΨΗΦΙΟΣ ΚΩΔΙΚΟΣ ΚΡΑΤΗΣΗΣ ΕΙΝΑΙ: {booking_code}."

    except Exception as e:

        return f"ERROR: Αποτυχία εγγραφής: {str(e)}"



def cancel_calendar_event(booking_code_or_phone):

    try:

        target_event = search_event_in_calendar(booking_code_or_phone)

        if not target_event:

            return f"ERROR_NOT_FOUND: ΔΕΝ βρέθηκε ραντεβού με τον κωδικό/τηλέφωνο '{booking_code_or_phone}'."



        calendar_service.events().delete(calendarId=CALENDAR_ID, eventId=target_event['id']).execute()

        return "SUCCESS: Το ραντεβού διαγράφηκε επιτυχώς."

    except Exception as e:

        return f"ERROR: Αποτυχία ακύρωσης: {str(e)}"



def reschedule_calendar_event(booking_code_or_phone, new_start_iso, new_end_iso):

    try:

        athens_tz = zoneinfo.ZoneInfo("Europe/Athens")

        new_start_iso = new_start_iso.replace(" ", "T")

        new_end_iso = new_end_iso.replace(" ", "T")

        if len(new_start_iso) == 16: new_start_iso += ":00"

        if len(new_end_iso) == 16: new_end_iso += ":00"



        dt_new_start = datetime.datetime.fromisoformat(new_start_iso).replace(tzinfo=athens_tz)

        dt_new_end = datetime.datetime.fromisoformat(new_end_iso).replace(tzinfo=athens_tz)



        is_valid, msg = is_within_working_hours(dt_new_start)

        if not is_valid:

            return f"ERROR_OUT_OF_HOURS: {msg}"



        target_event = search_event_in_calendar(booking_code_or_phone)

        if not target_event:

            return f"ERROR_NOT_FOUND: Δεν βρέθηκε ραντεβού με τον κωδικό/τηλέφωνο '{booking_code_or_phone}'."



        target_event['start'] = {'dateTime': dt_new_start.isoformat(), 'timeZone': 'Europe/Athens'}

        target_event['end'] = {'dateTime': dt_new_end.isoformat(), 'timeZone': 'Europe/Athens'}



        calendar_service.events().update(calendarId=CALENDAR_ID, eventId=target_event['id'], body=target_event).execute()

        return f"SUCCESS: Το ραντεβού μεταφέρθηκε επιτυχώς στη νέα ώρα: {dt_new_start.strftime('%d/%m/%Y %H:%M')}!"

    except Exception as e:

        return f"ERROR: Αποτυχία μεταφοράς: {str(e)}"
