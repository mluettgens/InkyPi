from blueprints import settings
from plugins.base_plugin.base_plugin import BasePlugin
from PIL import Image
import os
import requests
import logging
from datetime import datetime, timezone
import pytz
from io import BytesIO
import math
import json

logger = logging.getLogger(__name__)

WEATHER_URL = "https://api.openweathermap.org/data/3.0/onecall?lat={lat}&lon={long}&units={units}&exclude=minutely&appid={api_key}"
GEOCODING_URL = "http://api.openweathermap.org/geo/1.0/reverse?lat={lat}&lon={long}&limit=1&appid={api_key}"

class Dashboard(BasePlugin):

    def generate_settings_template(self):
        template_params = super().generate_settings_template()
        template_params['api_key'] = {
            "required": True,
            "service": "OpenWeatherMap",
            "expected_key": "OPEN_WEATHER_MAP_SECRET"
        }
        template_params['style_settings'] = True
        return template_params

    def generate_image(self, settings, device_config):
        dimensions = device_config.get_resolution()
        if device_config.get_config("orientation") == "vertical":
            dimensions = dimensions[::-1]

        template_params = {"plugin_settings": settings}

        timezone = device_config.get_config("timezone", default="America/New_York")
        time_format = device_config.get_config("time_format", default="12h")

        # Get today's Losung
        todays_losung = self.get_todays_losung()
        if todays_losung:
            template_params["losung"] = todays_losung

        # News abrufen
        news_count = int(settings.get('news_count', 4))
        news_feed = settings.get('news_feed', 'tagesschau')
        news_items = self.get_news_feed(news_feed, news_count)
        template_params["news"] = news_items

        # Outlook-Termine abrufen
        template_params["calendar"] = self.get_outlook_events(device_config, settings, timezone)

        if not template_params["calendar"]:
            logger.warning("Keine Outlook-Termine gefunden oder Fehler beim Abrufen.")

        # Wetter abrufen (Icon + Temperatur)
        lat = settings.get('latitude')
        long = settings.get('longitude')
        weather_key = device_config.load_env_key("OPEN_WEATHER_MAP_SECRET")

        weather = self.get_current_weather(weather_key, lat, long)
        if weather:
            template_params["weather"] = weather

        template_params["info"] = self.get_info(weather_key, lat, long, device_config)

        image = self.render_image(dimensions, "dashboard.html", "dashboard.css", template_params)

        if not image:
            raise RuntimeError("Failed to take screenshot, please check logs.")
        return image

    def get_todays_losung(self):
        """Liest die heutige Losung aus der losungen.json und gibt sie als Dict zurück."""
        losungen_path = os.path.join(os.path.dirname(__file__), "resources", "losungen.json")
        today = datetime.now().strftime("%d.%m.%Y")
        with open(losungen_path, encoding="utf-8") as f:
            data = json.load(f)
        for entry in data:
            if entry.get("Datum") == today:
                return entry
        return None
    
    def get_news_feed(self, feed, max_items=5):
        """Holt die neuesten Nachrichten aus dem gewählten RSS-Feed als Liste von Dicts (title, description, image).
        Für 'golem' wird content:encoded bevorzugt und die Description stärker bereinigt (IMG-Tags entfernt, HTML entfernt,
        parenthetische Gruppen mit Links/HTML entfernt). Für andere Feeds wird die description einfach von HTML befreit.
        """
        import xml.etree.ElementTree as ET
        import re
        import html as _html
        feeds = {
            "tagesschau": "https://www.tagesschau.de/xml/rss2/",
            "golem": "https://rss.golem.de/rss.php?feed=RSS2.0"
        }
        url = feeds.get(feed, feeds["tagesschau"])
        try:
            resp = requests.get(url, timeout=10)
            resp.raise_for_status()
            root = ET.fromstring(resp.content)
            items = []
            for item in root.findall('.//item'):
                if len(items) >= max_items:
                    break
                title = (item.findtext('title') or '').strip()
                # skip promotional/advertisement items where title begins with 'Anzeige:' (case-insensitive)
                if title and title.lower().startswith('anzeige:'):
                    logger.debug(f"Skipping advertised item with title: {title}")
                    continue

                description = item.findtext('description') or ''
                content_encoded = item.find('{http://purl.org/rss/1.0/modules/content/}encoded')

                # choose processing path
                if feed == 'golem' and content_encoded is not None and content_encoded.text:
                    original_html = content_encoded.text
                    # extract image from content:encoded
                    image_url = self.extract_image_url(original_html)

                    # remove all <img> tags (including tracking 1x1 images)
                    no_img = re.sub(r'<img[^>]*>', '', original_html, flags=re.IGNORECASE)

                    # remember parenthetical groups that contained HTML or links so we can remove them later
                    paren_groups = []
                    for m in re.finditer(r'\([^)]*\)', original_html):
                        grp = m.group(0)
                        if '<' in grp or 'http' in grp or '&lt;' in grp:
                            cleaned_grp = re.sub(r'<[^>]+>', '', grp)
                            cleaned_grp = _html.unescape(cleaned_grp)
                            paren_groups.append(cleaned_grp.strip())

                    # strip remaining HTML and unescape
                    text = re.sub(r'<[^>]+>', '', no_img)
                    text = _html.unescape(text)

                    # remove remembered parenthetical groups
                    for pg in paren_groups:
                        if pg:
                            text = text.replace(pg, '')

                    text = re.sub(r'\s+', ' ', text).strip()
                else:
                    # generic/simple cleaning for other feeds (e.g. tagesschau)
                    image_url = self.extract_image_url(content_encoded.text)
                    text = description

                items.append({"title": title, "description": text, "image": image_url})

            logger.debug(f"News: {len(items)} Nachrichten geladen.")
            if items:
                logger.debug(f"Erste Nachricht: {items[0]}")
            else:
                logger.warning("News: Keine Nachrichten gefunden!")
            return items
        except Exception as e:
            logger.error(f"RSS-Feed konnte nicht geladen werden: {e}")
            return []

    @staticmethod
    def extract_image_url(html_content):
        import re
        if not html_content:
            return None
        match = re.search(r'<img\s+src="([^"]+)"', html_content)
        if match:
            url = match.group(1)
            url = re.sub(r'width=\d+', 'width=320', url)
            return url
        return None

    def get_outlook_events(self, device_config, settings, timezone):        
        """
        Holt Outlook-Termine für die nächsten 'days' Tage ab heute via Microsoft Graph API.
        Gibt eine Liste von Dicts mit subject, start, end, location, organizer, attendees zurück.
        """
        import msal
        import requests
        from datetime import datetime, timedelta

        client_id = device_config.load_env_key("OUTLOOK_CLIENT_ID")
        client_secret = device_config.load_env_key("OUTLOOK_CLIENT_SECRET")
        tenant_id = device_config.load_env_key("OUTLOOK_TENANT_ID")
        user_email = device_config.load_env_key("OUTLOOK_USER_EMAIL")

        if not all([client_id, client_secret, tenant_id, user_email]):
            logger.error("Fehlende Outlook-Konfigurationsparameter.")
            return []

        tz = pytz.timezone(timezone)
        current_dt = datetime.now(tz)

        # Calendar day offset override (auto / 0 / 1)
        day_offset_setting = settings.get('calendar_day_offset', 'auto')

        # Determine day_offset based on device timezone and setting
        tz = pytz.timezone(timezone)
        current_dt = datetime.now(tz)
        if day_offset_setting == 'auto':
            day_offset = 0 if current_dt.hour < 18 else 1
        else:
            try:
                day_offset = int(day_offset_setting)
                if day_offset not in (0,1):
                    day_offset = 0
            except Exception:
                day_offset = 0

        # Calendar display range (defaults)
        start_hour = int(settings.get('calendar_start_hour', 8))
        end_hour = int(settings.get('calendar_end_hour', 18))


        authority = f"https://login.microsoftonline.com/{tenant_id}"
        scope = ["https://graph.microsoft.com/.default"]

        app = msal.ConfidentialClientApplication(
            client_id,
            authority=authority,
            client_credential=client_secret
        )

        result = app.acquire_token_for_client(scopes=scope)
        if "access_token" not in result:
            logger.error(f"Fehler beim Authentifizieren: {result.get('error_description')}")
            return []

        headers = {
            "Authorization": f"Bearer {result['access_token']}",
            "Accept": "application/json",
            "Prefer": f'outlook.timezone="{timezone}"'
        }

        # Zeitraum: ein Tagesbereich im angegebenen timezone, offset durch day_offset (0=heute,1=morgen)
        try:
            import pytz as _pytz
            tz_local = _pytz.timezone(timezone)
        except Exception:
            tz_local = pytz.UTC

        local_start = datetime.now(tz_local).replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=day_offset)
        local_end = local_start + timedelta(days=days)

        # Graph benötigt UTC ISO-Strings; konvertiere und verwende Z-Suffix
        start_utc = local_start.astimezone(pytz.UTC)
        end_utc = local_end.astimezone(pytz.UTC)
        start_iso = start_utc.isoformat().replace('+00:00', 'Z')
        end_iso = end_utc.isoformat().replace('+00:00', 'Z')

        logger.debug(f"Outlook query range (local {timezone}): {local_start.isoformat()} - {local_end.isoformat()} (day_offset={day_offset})")

        url = (
            f"https://graph.microsoft.com/v1.0/users/{user_email}/calendarView"
            f"?startDateTime={start_iso}"
            f"&endDateTime={end_iso}"
            f"&$select=subject,start,end,location,isallday"
            f"&$orderby=start/dateTime"
            f"&$top=50"
        )

        try:
            resp = requests.get(url, headers=headers, timeout=10)
            resp.raise_for_status()

            events = resp.json().get("value", [])
            logger.info(f"{len(events)} Outlook-Termine geladen.")

            parsed_events = []
            for e in events:

                start_dt = datetime.fromisoformat(e.get("start", {}).get("dateTime"))
                starttime = start_dt.strftime("%H:%M") if start_dt else None
                hours, minutes = map(int, starttime.split(":"))
                total_start = hours * 60 + minutes - (start_hour * 60)

                end_dt = datetime.fromisoformat(e.get("end", {}).get("dateTime"))
                endtime = end_dt.strftime("%H:%M") if end_dt else None
                hours, minutes = map(int, endtime.split(":"))
                total_end = hours * 60 + minutes - (start_hour * 60)

                logger.info(f"Termine {str(e.get('subject'))}. Ganztägig: {e.get('isAllDay', False)}")

                parsed_event = {
                    'id': str(e.get("id")),
                    'start': starttime or '',
                    'end': endtime or '',
                    'title': str(e.get("subject")),
                    'start_min': total_start,
                    'end_min': total_end,
                    'is_all_day': e.get("isAllDay", False)
                }

                parsed_events.append(parsed_event)


            logger.info(f"Parsed Outlook events: {parsed_events}")

            #return parsed_events

            return {
                "day_label": "Heute" if day_offset == 0 else "Morgen",
                "start_hour": start_hour,
                "end_hour": end_hour,
                "events": parsed_events
            }
        except Exception as e:
            logger.error(f"Fehler beim Abrufen der Outlook-Termine: {e}")
            return []
        
    def get_current_weather(self, api_key, lat, long):
        """Holt aktuelles Wetter (Icon+Temp) von OpenWeatherMap. Liefert Dict mit 'icon', 'temp', 'location'."""

        logger.info(f"Weather request for coordinates: {lat}, {long}")

        url = WEATHER_URL.format(lat=lat, long=long, units="metric", api_key=api_key)
        
        response = requests.get(url)

        if not 200 <= response.status_code < 300:
            logging.error(f"Failed to retrieve weather data: {response.content}")
            raise RuntimeError("Failed to retrieve weather data.")

        data = response.json()

        logging.info(f"Weather data retrieved successfully: {data}")

        temp = round(data["current"]["temp"])
        weather_icon = self.get_plugin_dir(f'icons/{data["current"]["weather"][0]["icon"]}.svg')

        return {"icon": weather_icon, "temp": temp}
        
    def get_location(self, api_key, lat, long):
        url = GEOCODING_URL.format(lat=lat, long=long, units="metric", api_key=api_key)
        response = requests.get(url)

        if not 200 <= response.status_code < 300:
            logging.error(f"Failed to get location: {response.content}")
            raise RuntimeError("Failed to retrieve location.")

        location_data = response.json()[0]
        location_str = f"{location_data.get('name')}"

        logger.info(f"Location retrieved: {location_str}")

        return location_str        

    def get_info(self, api_key, lat, long, device_config):
        """
        Gibt ein Dict mit 'location', 'last_refresh_time' und 'date' (deutsches Format) zurück.
        """
        location = self.get_location(api_key, lat, long)

        timezone = device_config.get_config("timezone", default="Europe/Berlin")
        time_format = device_config.get_config("time_format", default="12h")
        import pytz
        tz = pytz.timezone(timezone)
        now = datetime.now(tz)
        if time_format == "24h":
            last_refresh_time = now.strftime("%H:%M")
        else:
            last_refresh_time = now.strftime("%I:%M %p")

        # Deutsches Wochentag-Mapping
        weekdays = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"]
        weekday = weekdays[now.weekday()]
        date_str = f"{weekday}, {now.strftime('%d.%m.%Y')}"

        # Begrüssung nach Tageszeit
        hour = now.hour
        if hour < 5:
            greeting = "Gute Nacht"
        elif hour < 11:
            greeting = "Guten Morgen"
        elif hour < 17:
            greeting = "Guten Tag"
        elif hour < 22:
            greeting = "Guten Abend"
        else:
            greeting = "Gute Nacht"


        return {
            "location": location,
            "last_refresh_time": last_refresh_time,
            "date": date_str,
            "greeting": greeting
        }