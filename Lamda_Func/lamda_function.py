# -*- coding: utf-8 -*-
import logging
import re
import os
import boto3
import hashlib
from datetime import date as _date, timedelta, datetime

import ask_sdk_core.utils as ask_utils
from ask_sdk_core.skill_builder import SkillBuilder
from ask_sdk_core.dispatch_components import (
    AbstractRequestHandler,
    AbstractExceptionHandler,
)

# Configure logging for CloudWatch
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# ---------- DynamoDB ----------
# Connect to DynamoDB using the table name from environment variable
dynamodb = boto3.resource("dynamodb")
TABLE_NAME = os.getenv("TABLE_NAME", "PanicAttacks")
table = dynamodb.Table(TABLE_NAME)

# ---------- PSEUDONYMIZATION ----------
# Salt value used for hashing the Alexa user ID.
# IMPORTANT: In production, always set USER_HASH_SALT as an environment variable.
# Never rely on the default fallback value in a live system.
USER_HASH_SALT = os.getenv("USER_HASH_SALT", "panictrace_salt")

def pseudonymize_user_id(raw_user_id: str) -> str:
    """
    Hashes the raw Alexa user ID with a salt using SHA-256.
    This ensures no personally identifiable information is stored in the database.
    """
    if not raw_user_id:
        return "unknown"
    salted = raw_user_id + USER_HASH_SALT
    return hashlib.sha256(salted.encode("utf-8")).hexdigest()


# ---------- DSM-5 PANIC SYMPTOMS ----------
# All 13 DSM-5 defined panic attack symptoms used for binary feature encoding
ALL_SYMPTOMS = [
    "palpitations",
    "sweating",
    "trembling",
    "shortness_of_breath",
    "chest_pain",
    "nausea",
    "dizziness",
    "fear_of_dying",
    "fear_losing_control",
    "paresthesia",
    "chills_or_heat",
    "derealization",
    "depersonalization"
]


# ---------- STOPWORDS ----------
# Comprehensive list of words that carry no clinical meaning.
# These are filtered out before storing unmapped_terms so that only
# medically relevant words that did not match a DSM-5 pattern are retained.
# Categories:
#   - Personal pronouns    : i, me, my, myself, we, you, your ...
#   - Articles/determiners : a, an, the, this, that, these, those ...
#   - Common verbs         : am, is, was, have, had, feel, felt ...
#   - Prepositions         : in, on, at, to, from, with, about ...
#   - Conjunctions         : and, or, but, so, because ...
#   - Adverbs/intensifiers : very, quite, really, suddenly, also ...
#   - Quantifiers          : some, any, no, much, more, few ...
#   - Filler/hedge words   : like, just, kind, sort, bit, little ...
#   - Time expressions     : today, yesterday, now, then, when ...
#   - Negations            : not, never, no, neither, nor ...
STOPWORDS = {
    # Personal pronouns
    "i", "me", "my", "myself", "we", "our", "ours", "ourselves",
    "you", "your", "yours", "yourself", "yourselves",
    "he", "him", "his", "himself", "she", "her", "hers", "herself",
    "it", "its", "itself", "they", "them", "their", "theirs", "themselves",

    # Articles and determiners
    "a", "an", "the", "this", "that", "these", "those",
    "each", "every", "either", "neither", "both", "all", "any", "some",
    "such", "what", "which", "whose",

    # Common auxiliary and linking verbs
    "am", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "having",
    "do", "does", "did", "doing",
    "will", "would", "shall", "should",
    "may", "might", "must", "can", "could",
    "need", "dare", "ought",

    # Common main verbs related to symptom reporting
    "feel", "felt", "feeling",
    "get", "got", "getting",
    "seem", "seemed", "seems",
    "start", "started", "starting",
    "become", "became", "becomes",
    "notice", "noticed", "noticing",
    "experience", "experienced", "experiencing",
    "go", "went", "going",
    "come", "came", "coming",
    "think", "thought", "thinking",
    "know", "knew", "knowing",
    "keep", "kept", "keeping",
    "make", "made", "making",
    "say", "said", "saying",
    "happen", "happened", "happening",

    # Prepositions
    "in", "on", "at", "to", "from", "with", "without", "about",
    "above", "below", "between", "into", "through", "during",
    "before", "after", "since", "until", "up", "down", "out",
    "over", "under", "again", "off", "of", "for", "by", "as",

    # Conjunctions
    "and", "or", "but", "so", "yet", "nor", "for",
    "because", "although", "though", "while", "whereas",
    "if", "unless", "until", "when", "whenever", "where",
    "that", "than", "whether",

    # Adverbs and intensifiers
    "very", "quite", "really", "extremely", "incredibly",
    "suddenly", "quickly", "slowly", "immediately", "gradually",
    "also", "too", "even", "still", "already", "almost", "just",
    "only", "exactly", "actually", "basically", "generally",
    "especially", "particularly", "mainly", "mostly",
    "always", "never", "sometimes", "often", "usually", "rarely",
    "here", "there", "now", "then", "today", "yesterday",
    "recently", "suddenly", "normally", "especially",

    # Quantifiers and degree words
    "much", "many", "more", "most", "less", "least", "few", "little",
    "enough", "rather", "pretty", "fairly", "somewhat", "slightly",
    "bit", "lot", "lots",

    # Filler and hedge words commonly used in symptom descriptions
    "like", "kind", "sort", "type", "way", "thing", "things",
    "something", "anything", "nothing", "everything",
    "somehow", "anyway", "maybe", "perhaps", "probably",

    # Negations
    "not", "no", "nor", "neither", "never", "none",
    "cannot", "cant", "couldnt", "wouldnt", "shouldnt", "wasnt",
    "werent", "hadnt", "havent", "hasnt", "didnt", "dont", "doesnt",

    # Numbers and vague quantities (written out)
    "one", "two", "three", "four", "five",
    "first", "second", "third",

    # Common filler expressions in spoken language
    "okay", "ok", "oh", "ah", "uh", "um", "well", "so", "right",
    "actually", "basically", "literally",

    # Punctuation that may survive tokenisation
    ",", ".", "!", "?", ";", ":", "-", "'", '"'
}


# ---------- INPUT VALIDATION ----------

def valid_date(value):
    """
    Validates and normalises the date input.
    Accepts 'today', 'yesterday', or ISO format (YYYY-MM-DD).
    Returns None if the date is invalid or lies in the future.
    """
    DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
    if not value:
        return None
    value = value.lower()
    if value == "today":
        return _date.today().isoformat()
    if value == "yesterday":
        return (_date.today() - timedelta(days=1)).isoformat()
    if DATE_RE.match(value):
        parsed = _date.fromisoformat(value)
        if parsed > _date.today():
            return None
        return value
    return None

def valid_time(value):
    """
    Validates the time input.
    Extracts HH:MM from the slot value returned by Alexa (AMAZON.TIME).
    Returns None if the format is unrecognised.
    """
    if not value:
        return None
    match = re.match(r"^(\d{2}:\d{2})", value)
    return match.group(1) if match else None

def valid_duration(value):
    """
    Validates the duration input.
    Accepts integer values between 1 and 180 minutes.
    Returns None if the value is out of range or cannot be parsed.
    """
    try:
        n = int(value)
        return n if 1 <= n <= 180 else None
    except:
        return None

def valid_severity(value):
    """
    Validates the severity rating.
    Accepts integer values between 1 and 10.
    Returns None if the value is out of range or cannot be parsed.
    """
    try:
        n = int(value)
        return n if 1 <= n <= 10 else None
    except:
        return None


# ---------- SYMPTOM EXTRACTION ----------

def extract_symptoms(handler_input):
    """
    Extracts DSM-5 panic symptoms from the user's free-text symptom description.

    Uses rule-based regex pattern matching to map spoken descriptions
    to the 13 predefined DSM-5 symptom categories.

    The unmapped_terms field captures words that:
      - did not match any DSM-5 symptom pattern
      - are not stopwords (e.g. 'i', 'have', 'a', 'and')
    This ensures only clinically relevant unknown terms are stored,
    such as 'headache' or 'back pain'.

    Returns:
        detected       (list) – matched DSM-5 symptom keys
        unmapped_terms (str)  – comma-separated clinically relevant unknown words
        raw_text       (str)  – original lowercased input text for traceability
    """
    request = handler_input.request_envelope.request
    if not hasattr(request, "intent"):
        return [], "", ""

    slots = request.intent.slots or {}
    symptom_slot = slots.get("symptoms")

    if not symptom_slot or not symptom_slot.value:
        return [], "", ""

    # Normalise input to lowercase for consistent pattern matching
    text = symptom_slot.value.lower()
    detected = []

    # Regex patterns for each of the 13 DSM-5 symptom categories
    patterns = {
        "palpitations":         r"\b(heart racing|racing heart|palpitations|pounding heart)\b",
        "sweating":             r"\b(sweating|sweaty|sweat)\b",
        "trembling":            r"\b(shaky|shaking|trembling|tremor)\b",
        "shortness_of_breath":  r"\b(short of breath|cant breathe|could not breathe|breathless|suffocating)\b",
        "chest_pain":           r"\b(chest pain|tight chest|pressure in chest)\b",
        "nausea":               r"\b(nausea|nauseous|vomiting|felt sick|stomachache|stomach ache|stomach pain|abdominal|belly ache)\b",
        "dizziness":            r"\b(dizzy|lightheaded|felt faint)\b",
        "fear_of_dying":        r"\b(dying|going to die|felt like i was dying)\b",
        "fear_losing_control":  r"\b(losing control|going crazy|out of control)\b",
        "paresthesia":          r"\b(numbness|tingling|pins and needles)\b",
        "chills_or_heat":       r"\b(chills|hot flashes|felt hot|felt cold)\b",
        "derealization":        r"\b(world felt unreal|unreal)\b",
        "depersonalization":    r"\b(out of body|felt detached|not myself)\b"
    }

    # Match each symptom pattern against the full input text
    for symptom, pattern in patterns.items():
        if re.search(pattern, text):
            detected.append(symptom)

    # --- Collect unmapped terms ---
    # Only store words that are:
    #   1. Not a stopword (no clinical meaning)
    #   2. Not part of a matched DSM-5 pattern
    # This gives us clean, clinically relevant unknown terms like "headache"
    unmapped_words = []
    for word in text.split():
        # Skip stopwords — they carry no clinical meaning
        if word in STOPWORDS:
            continue
        # Skip words that are part of a matched DSM-5 pattern
        matched = any(re.search(pattern, word) for pattern in patterns.values())
        if not matched:
            unmapped_words.append(word)

    unmapped_terms = ", ".join(unmapped_words) if unmapped_words else ""

    return detected, unmapped_terms, text


# ---------- LAUNCH HANDLER ----------

class LaunchRequestHandler(AbstractRequestHandler):
    """
    Handles the LaunchRequest, triggered when the user opens the skill
    without a specific command (e.g. 'Alexa, open PanicTrace').
    """
    def can_handle(self, handler_input):
        return ask_utils.is_request_type("LaunchRequest")(handler_input)

    def handle(self, handler_input):
        speak_output = (
            "Welcome to Panic Trace. "
            "You can document a panic attack now. "
            "You can start by saying: log a panic attack test."
        )
        return (
            handler_input.response_builder
            .speak(speak_output)
            .ask("For example, you can say: I felt dizzy and my heart was racing.")
            .response
        )


# ---------- MAIN INTENT HANDLER ----------

class PanicTraceIntentHandler(AbstractRequestHandler):
    """
    Handles the PanikTraceIntent — the primary intent for documenting a panic episode.

    Steps:
      1. Reads and validates all six slot values (date, time, duration,
         severity, trigger, symptoms)
      2. Extracts DSM-5 symptoms from the free-text symptom description
      3. Pseudonymizes the user ID before storage
      4. Builds and writes the structured record to DynamoDB
      5. Returns a full confirmation message to the user
    """
    def can_handle(self, handler_input):
        return ask_utils.is_intent_name("PanikTraceIntent")(handler_input)

    def handle(self, handler_input):

        request = handler_input.request_envelope.request
        slots = request.intent.slots or {}

        # Helper to safely read and strip a slot value
        def get(name):
            slot = slots.get(name)
            return slot.value.strip() if slot and slot.value else None

        # Read and validate all required slot values
        date_value     = valid_date(get("date"))
        time_value     = valid_time(get("time"))
        duration_value = valid_duration(get("duration"))
        severity_value = valid_severity(get("severity"))
        trigger_value  = get("trigger")

        # Extract DSM-5 symptoms, unmapped terms, and raw text from free-text input
        symptom_list, unmapped_terms, raw_text = extract_symptoms(handler_input)

        # If any required field failed validation, ask the user to try again
        if not all([date_value, time_value, duration_value, severity_value]):
            return handler_input.response_builder.speak(
                "Some information was invalid. Please try again."
            ).ask("When did the panic attack happen?").response

        # Pseudonymize the Alexa user ID — no raw IDs are stored
        try:
            raw_user_id = handler_input.request_envelope.context.system.user.user_id
        except Exception:
            raw_user_id = None

        user_id = pseudonymize_user_id(raw_user_id)

        # Build the DynamoDB record following the predefined flat schema
        item = {
            "userId":            user_id,
            "timestamp":         datetime.utcnow().isoformat(),
            "date":              date_value,
            "attack_hour":       int(time_value.split(":")[0]),
            "duration_min":      duration_value,
            "severity":          severity_value,
            "trigger":           trigger_value or "",
            "symptoms_raw_text": raw_text or "",
            # Only clinically relevant unknown words are stored here
            # Stopwords and matched DSM-5 terms are excluded
            "unmapped_terms":    unmapped_terms,
            "source":            "alexa"
        }

        # Encode each DSM-5 symptom as a binary feature (1 = present, 0 = absent)
        for symptom in ALL_SYMPTOMS:
            item[symptom] = 1 if symptom in symptom_list else 0

        # Persist the record to DynamoDB
        try:
            table.put_item(Item=item)
        except Exception as e:
            logger.error(f"DynamoDB write error: {e}")

        # Format detected symptom keys for natural speech (underscores → spaces)
        if symptom_list:
            symptoms_spoken = ", ".join(s.replace("_", " ") for s in symptom_list)
        else:
            symptoms_spoken = "none detected"

        # Format trigger for speech (fallback if not provided)
        trigger_spoken = trigger_value if trigger_value else "none reported"

        # Full confirmation message — all six attributes are read back to the user
        speak_output = (
            f"I recorded your panic attack on {date_value} "
            f"at {time_value}. "
            f"Duration: {duration_value} minutes. "
            f"Severity: {severity_value} out of 10. "
            f"Trigger: {trigger_spoken}. "
            f"Symptoms detected: {symptoms_spoken}."
        )

        return handler_input.response_builder.speak(speak_output).response


# ---------- FALLBACK HANDLER ----------

class FallbackIntentHandler(AbstractRequestHandler):
    """
    Handles the AMAZON.FallbackIntent, triggered when Alexa cannot match
    the user's input to any defined intent.
    """
    def can_handle(self, handler_input):
        return ask_utils.is_intent_name("AMAZON.FallbackIntent")(handler_input)

    def handle(self, handler_input):
        return (
            handler_input.response_builder
            .speak("I did not understand that.")
            .ask("Please describe your symptoms in your own words.")
            .response
        )


# ---------- EXCEPTION HANDLER ----------

class CatchAllExceptionHandler(AbstractExceptionHandler):
    """
    Global exception handler that catches any unhandled runtime errors.
    Logs the full stack trace to CloudWatch and returns a neutral error message
    without exposing internal system details to the user.
    """
    def can_handle(self, handler_input, exception):
        return True

    def handle(self, handler_input, exception):
        logger.error(exception, exc_info=True)
        return (
            handler_input.response_builder
            .speak("An unexpected error occurred. Please try again.")
            .response
        )


# ---------- SKILL BUILDER ----------
# Register all request handlers and the exception handler with the SkillBuilder
sb = SkillBuilder()
sb.add_request_handler(LaunchRequestHandler())
sb.add_request_handler(PanicTraceIntentHandler())
sb.add_request_handler(FallbackIntentHandler())
sb.add_exception_handler(CatchAllExceptionHandler())

# Entry point for the AWS Lambda function
lambda_handler = sb.lambda_handler()