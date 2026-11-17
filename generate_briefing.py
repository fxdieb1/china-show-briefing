import os
import html
import requests
import feedparser
import anthropic
from youtube_transcript_api import YouTubeTranscriptApi

PLAYLIST_ID = "PLGaYlBJIOoa94hnb-z2i64eVPcGZPUvo9"
PLAYLIST_RSS = f"https://www.youtube.com/feeds/videos.xml?playlist_id={PLAYLIST_ID}"
PROCESSED_FILE = "processed_ids.txt"

def load_processed_ids():
    if not os.path.exists(PROCESSED_FILE):
        return set()
    with open(PROCESSED_FILE, "r", encoding="utf-8") as f:
        return set(line.strip() for line in f if line.strip())

def save_processed_id(video_id):
    with open(PROCESSED_FILE, "a", encoding="utf-8") as f:
        f.write(f"{video_id}\n")

def get_full_transcript(video_id):
    try:
        ytt_api = YouTubeTranscriptApi()
        transcript = ytt_api.fetch(video_id)
        return " ".join(snippet.text for snippet in transcript)
    except Exception as e:
        print(f"Skipping {video_id} (no transcript yet): {e}")
        return None

def generate_podcast_script(title, published, raw_transcript):
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    
    prompt = f"""You are an executive financial podcast writer preparing a 30-minute morning audio briefing for a senior equity portfolio manager walking to work.

Turn the following transcript of "{title}" ({published}) into a cohesive, engaging, single-narrator spoken-word script.

STRICT RULES:
1. LENGTH: Target 4,300 to 4,600 words so it runs approximately 30 minutes when read aloud at 150 words per minute. Be thorough—cover every major market move, macro data point, company earnings breakdown, analyst debate, and geopolitical takeaway in detail rather than glossing over them.
2. AUDIO-FIRST FORMATTING: Write purely in natural spoken paragraphs. Do NOT use bullet points, markdown headers (# or **), tables, or URLs, as text-to-speech will read symbols awkwardly.
3. CLEAN UP TV ARTIFACTS: Strip out visual references ("as you can see on your screens", "looking at this chart"), commercial break bumpers, weather/traffic chatter, and technical audio dropouts.
4. SPELL OUT FINANCIAL SHORTHAND: Convert clunky ticker/symbol text into natural spoken English (e.g., write "the CSI 300 fell one point two percent" instead of "CSI 300 -1.2%", and spell out acronyms or basis points clearly).
5. STRUCTURE: Open with a crisp 60-second executive overview of the session's biggest drivers, then flow smoothly through thematic chapters using natural spoken transitions (e.g., "Turning from semiconductor export curbs to mainland tech earnings...").

RAW TRANSCRIPT:
{raw_transcript}
"""

    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=8192,
        messages=[{"role": "user", "content": prompt}]
    )
    return response.content[0].text

def get_elevenreader_id_token(refresh_token):
    # Fetch ElevenReader's public Firebase web API key from the app bundle
    app_page = requests.get("https://elevenreader.io", timeout=15).text
    import re
    match = re.search(r'AIzaSy[0-9A-Za-z_-]{33}', app_page)
    if not match:
        # Fallback: inspect main JS bundle if not inline
        scripts = re.findall(r'src="([^"]+\.js[^"]*)"', app_page)
        for s in scripts:
            url = s if s.startswith("http") else f"https://elevenreader.io{s}"
            js = requests.get(url, timeout=15).text
            match = re.search(r'AIzaSy[0-9A-Za-z_-]{33}', js)
            if match:
                break
    if not match:
        raise RuntimeError("Could not locate ElevenReader Firebase API key.")

    firebase_api_key = match.group(0)
    resp = requests.post(
        f"https://securetoken.googleapis.com/v1/token?key={firebase_api_key}",
        json={"grant_type": "refresh_token", "refresh_token": refresh_token},
        timeout=15
    )
    resp.raise_for_status()
    return resp.json()["id_token"]

def push_to_elevenreader(title, script_text):
    id_token = get_elevenreader_id_token(os.environ["ELEVEN_REFRESH_TOKEN"])
    
    paragraphs = [p.strip() for p in script_text.split("\n\n") if p.strip()]
    html_body = "".join(f"<p>{html.escape(p)}</p>" for p in paragraphs)
    html_doc = (
        f"<!DOCTYPE html><html><head><meta charset='utf-8'>"
        f"<title>{html.escape(title)}</title></head>"
        f"<body><h1>{html.escape(title)}</h1>{html_body}</body></html>"
    )

    files = {
        "file": ("briefing.html", html_doc.encode("utf-8"), "text/html")
    }
    data = {
        "name": f"{title} (30-Min Briefing)"
    }
    headers = {
        "Authorization": f"Bearer {id_token}"
    }
    resp = requests.post(
        "https://api.elevenlabs.io/v1/reader/reads/add",
        headers=headers,
        data=data,
        files=files,
        timeout=30
    )
    print(f"ElevenReader API status: {resp.status_code} | {resp.text[:300]}")
    resp.raise_for_status()
    print(f"Uploaded '{title}' to ElevenReader library.")

def main():
    processed = load_processed_ids()
    feed = feedparser.parse(PLAYLIST_RSS)

    for entry in feed.entries[:5]:
        video_id = entry.yt_videoid
        if video_id in processed:
            continue

        raw_text = get_full_transcript(video_id)
        if not raw_text:
            continue

        word_count = len(raw_text.split())
        if word_count < 6000:
            print(f"Skipping short clip {video_id} ({word_count} words)")
            save_processed_id(video_id)
            continue

        print(f"Processing full episode: {entry.title} ({word_count} words)...")
        script = generate_podcast_script(entry.title, entry.published, raw_text)
        push_to_elevenreader(entry.title, script)
        save_processed_id(video_id)
        break

if __name__ == "__main__":
    main()
