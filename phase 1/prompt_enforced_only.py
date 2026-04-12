import os
import json
import requests
from dotenv import load_dotenv
from datetime import datetime

load_dotenv()

API_KEY = os.getenv("OPENROUTER_API_KEY")
API_URL = "https://openrouter.ai/api/v1/chat/completions"

MODELS = [
    # "openai/gpt-4o-mini",
    "openai/gpt-5.3-chat",
    "openai/gpt-5.4",
    "google/gemini-3.1-pro-preview",
    "anthropic/claude-sonnet-4.6",
]

SCHEMA_FILE = "eos_designs.schema.yml"

# No API-level JSON mode is used here. Output format is enforced purely through
# prompt engineering: explicit format instructions, structural anchoring with a
# skeleton, chain-of-thought inside a "reasoning" key, and a hard closing
# reminder. This mirrors the classic "prompt-only" JSON extraction technique.
SYSTEM_PROMPT = (
    "You are a network engineer specializing in Arista AVD (Arista Validated Designs) "
    "and EOS configurations. "
    "Your response MUST be a single raw JSON object containing only the network configuration — "
    "no reasoning, no explanations, no markdown, no code fences, no text before or after the JSON. "
    "Every response you give will be passed directly to json.loads() in Python; "
    "any character outside the JSON object will cause a parse error. "
    "Use the EOS Designs schema provided to ensure field names, types, and structure are correct. "
    "Output ONLY the configuration as a raw JSON object, nothing else."
)

# User prompts include an explicit closing reminder to reinforce JSON-only output
_JSON_REMINDER = (
    "\n\nIMPORTANT: Your entire response must be a single valid JSON object "
    "containing only the network configuration. No reasoning, no markdown, no extra text."
)

PROMPT_VARIANTS = [
    "Generate a JSON object with the AVD configuration to set the BGP router-id using Loopback0 with IP 10.10.10.1/32 on a leaf switch" + _JSON_REMINDER,
    "Generate a JSON object with the AVD configuration to set the VXLAN source interface to Loopback1" + _JSON_REMINDER,
    "Generate a minimal JSON object with the AVD configuration for VLAN 30 with VNI 1030, overriding the EVPN route-target to 65000:1030" + _JSON_REMINDER,
    "Generate a minimal JSON object with the AVD configuration for a BGP peering with neighbor 10.0.0.2 remote-as 65100" + _JSON_REMINDER,
]

OUTPUT_FILE = "output_prompt_enforced.txt"


def load_schema():
    with open(SCHEMA_FILE, "r") as f:
        return f.read()


def build_system_prompt(schema_content):
    return (
        SYSTEM_PROMPT + "\n\n"
        "Below is the EOS Designs schema for reference. Use it to ensure your JSON output "
        "is valid and conforms to the correct field names, types, and structure.\n\n"
        "<eos_designs_schema>\n" + schema_content + "\n</eos_designs_schema>"
        "\n\nFinal reminder: respond with ONLY the raw JSON configuration object. No reasoning. No markdown. No extra text."
    )


def run_model(model, user_prompt, system_prompt_with_schema):
    messages = [
        {"role": "system", "content": system_prompt_with_schema},
        {"role": "user", "content": user_prompt},
    ]
    # No response_format parameter — output discipline enforced by prompt only
    payload = {
        "model": model,
        "messages": messages,
    }
    response = requests.post(
        API_URL,
        headers={
            "Authorization": "Bearer " + API_KEY,
            "Content-Type": "application/json",
        },
        data=json.dumps(payload),
    )
    if not response.ok:
        raise Exception(str(response.status_code) + " " + response.reason + " - " + response.text)
    raw_content = response.json()["choices"][0]["message"]["content"]
    # Attempt to pretty-print; flag if model failed to return valid JSON
    try:
        parsed = json.loads(raw_content)
        return messages, json.dumps(parsed, indent=2), True
    except json.JSONDecodeError:
        return messages, raw_content, False


def write_prompt_header(f, prompt_index, user_prompt):
    # Strip the inline reminder from the header for readability
    display_prompt = user_prompt.split("\n\nIMPORTANT:")[0]
    f.write("#" * 70 + "\n")
    f.write("# PROMPT " + str(prompt_index) + "\n")
    f.write("# " + display_prompt + "\n")
    f.write("#" * 70 + "\n\n")


def write_model_response(f, model, messages, response_text, is_valid_json):
    f.write("=" * 60 + "\n")
    f.write("Timestamp  : " + datetime.now().isoformat() + "\n")
    f.write("Model      : " + model + "\n")
    f.write("Valid JSON : " + ("YES" if is_valid_json else "NO (model deviated from prompt)") + "\n")
    f.write("-" * 60 + "\n")
    f.write("User prompt sent:\n")
    user_content = next(m["content"] for m in messages if m["role"] == "user")
    display_content = user_content.split("\n\nIMPORTANT:")[0]
    f.write("  " + display_content + "\n")
    f.write("-" * 60 + "\n")
    f.write("Response (prompt-enforced JSON):\n")
    f.write(response_text + "\n")
    f.write("=" * 60 + "\n\n")


def write_model_error(f, model, error):
    f.write("=" * 60 + "\n")
    f.write("Timestamp : " + datetime.now().isoformat() + "\n")
    f.write("Model     : " + model + "\n")
    f.write("ERROR     : " + str(error) + "\n")
    f.write("=" * 60 + "\n\n")


def main():
    print("Loading schema...")
    schema_content = load_schema()
    system_prompt_with_schema = build_system_prompt(schema_content)
    print("Schema loaded (" + str(len(schema_content)) + " chars). Starting runs...\n")

    with open(OUTPUT_FILE, "a") as f:
        f.write("\n" + "#" * 70 + "\n")
        f.write("# [PROMPT-ENFORCED JSON ONLY] Run started: " + datetime.now().isoformat() + "\n")
        f.write("#" * 70 + "\n\n")

    for i, user_prompt in enumerate(PROMPT_VARIANTS, start=1):
        display_prompt = user_prompt.split("\n\nIMPORTANT:")[0]
        print("[Prompt " + str(i) + "/" + str(len(PROMPT_VARIANTS)) + "] " + display_prompt)

        with open(OUTPUT_FILE, "a") as f:
            write_prompt_header(f, i, user_prompt)

        for model in MODELS:
            print("  -> " + model + "...", end=" ", flush=True)
            try:
                messages, response_text, is_valid_json = run_model(model, user_prompt, system_prompt_with_schema)
                with open(OUTPUT_FILE, "a") as f:
                    write_model_response(f, model, messages, response_text, is_valid_json)
                status = "done (valid JSON)." if is_valid_json else "done (WARNING: invalid JSON)."
                print(status)
            except Exception as e:
                print("ERROR: " + str(e))
                with open(OUTPUT_FILE, "a") as f:
                    write_model_error(f, model, e)

        print()

    print("All done. Results written to " + OUTPUT_FILE)


if __name__ == "__main__":
    main()
