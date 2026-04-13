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


SYSTEM_PROMPT = (
    "You are a network engineer specializing in Arista AVD (Arista Validated Designs) "
    "and EOS configurations. "
    "You must respond with a single, valid JSON object only. "
    "Do not include markdown fences, prose, or any text outside the JSON object. "
    "Use the EOS Designs schema provided to ensure field names, types, and structure "
    "are correct. Represent the AVD configuration as a JSON object that mirrors the "
    "structure of an AVD YAML file converted to JSON."
)

# Prompts updated to ask for JSON output instead of YAML
PROMPT_VARIANTS = [
    "Generate a JSON object with the AVD configuration to set the BGP router-id using Loopback0 with IP 10.10.10.1/32 on a leaf switch",
    "Generate a JSON object with the AVD configuration to set the VXLAN source interface to Loopback1",
    "Generate a minimal JSON object with the AVD configuration for VLAN 30 with VNI 1030, overriding the EVPN route-target to 65000:1030",
    "Generate a minimal JSON object with the AVD configuration for a BGP peering with neighbor 10.0.0.2 remote-as 65100",
]

OUTPUT_FILE = "output_json_enforced.txt"


def load_schema():
    with open(SCHEMA_FILE, "r") as f:
        return f.read()


def build_system_prompt(schema_content):
    return (
        SYSTEM_PROMPT + "\n\n"
        "Below is the EOS Designs schema for reference. Use it to ensure your JSON output "
        "is valid and conforms to the correct field names, types, and structure.\n\n"
        "<eos_designs_schema>\n" + schema_content + "\n</eos_designs_schema>"
    )


def run_model(model, user_prompt, system_prompt_with_schema):
    messages = [
        {"role": "system", "content": system_prompt_with_schema},
        {"role": "user", "content": user_prompt},
    ]
    payload = {
        "model": model,
        "response_format": {"type": "json_object"},
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
    # Pretty-print if the response is valid JSON, otherwise pass through as-is
    try:
        parsed = json.loads(raw_content)
        return messages, json.dumps(parsed, indent=2)
    except json.JSONDecodeError:
        return messages, raw_content


def write_prompt_header(f, prompt_index, user_prompt):
    f.write("#" * 70 + "\n")
    f.write("# PROMPT " + str(prompt_index) + "\n")
    f.write("# " + user_prompt + "\n")
    f.write("#" * 70 + "\n\n")


def write_model_response(f, model, messages, response_text):
    f.write("=" * 60 + "\n")
    f.write("Timestamp : " + datetime.now().isoformat() + "\n")
    f.write("Model     : " + model + "\n")
    f.write("-" * 60 + "\n")
    f.write("User prompt sent:\n")
    user_content = next(m["content"] for m in messages if m["role"] == "user")
    f.write("  " + user_content + "\n")
    f.write("-" * 60 + "\n")
    f.write("Response (JSON mode enforced):\n")
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
        f.write("# [JSON MODE ENFORCED] Run started: " + datetime.now().isoformat() + "\n")
        f.write("#" * 70 + "\n\n")

    for i, user_prompt in enumerate(PROMPT_VARIANTS, start=1):
        print("[Prompt " + str(i) + "/" + str(len(PROMPT_VARIANTS)) + "] " + user_prompt)

        with open(OUTPUT_FILE, "a") as f:
            write_prompt_header(f, i, user_prompt)

        for model in MODELS:
            print("  -> " + model + "...", end=" ", flush=True)
            try:
                messages, response_text = run_model(model, user_prompt, system_prompt_with_schema)
                with open(OUTPUT_FILE, "a") as f:
                    write_model_response(f, model, messages, response_text)
                print("done.")
            except Exception as e:
                print("ERROR: " + str(e))
                with open(OUTPUT_FILE, "a") as f:
                    write_model_error(f, model, e)

        print()

    print("All done. Results written to " + OUTPUT_FILE)


if __name__ == "__main__":
    main()
