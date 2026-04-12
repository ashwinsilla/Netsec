import os
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

SYSTEM_PROMPT = "You are a helpful assistant that generates YAML configurations for Arista VXLAN Fabric (AVD) and EOS."

PROMPT_VARIANTS = [
    "Generate AVD YAML to configure BGP router-id using Loopback0 with IP 10.10.10.1/32 on a leaf switch",
    "Generate AVD YAML to configure VXLAN source interface Loopback1",
    "Generate minimal AVD YAML to configure VLAN 30 with VNI 1030 and override the EVPN route-target to 65000:1030",
    "Generate minimal AVD YAML to configure BGP peering with neighbor 10.0.0.2 remote-as 65100",
]

OUTPUT_FILE = "output.txt"


def load_schema():
    with open(SCHEMA_FILE, "r") as f:
        return f.read()


def build_system_prompt(schema_content):
    return (
        f"{SYSTEM_PROMPT}\n\n"
        "Below is the EOS Designs schema for reference. Use it to ensure your YAML output is valid and conforms to the correct field names, types, and structure.\n\n"
        f"<eos_designs_schema>\n{schema_content}\n</eos_designs_schema>"
    )


def run_model(model, user_prompt, system_prompt_with_schema):
    messages = [
        {"role": "system", "content": system_prompt_with_schema},
        {"role": "user", "content": user_prompt},
    ]
    response = requests.post(
        API_URL,
        headers={
            "Authorization": f"Bearer {API_KEY}",
            "Content-Type": "application/json",
        },
        json={"model": model, "messages": messages},
    )
    if not response.ok:
        raise Exception(f"{response.status_code} {response.reason} — {response.text}")
    return messages, response.json()["choices"][0]["message"]["content"]


def write_prompt_header(f, prompt_index, user_prompt):
    f.write("#" * 70 + "\n")
    f.write(f"# PROMPT {prompt_index}\n")
    f.write(f"# {user_prompt}\n")
    f.write("#" * 70 + "\n\n")


def write_model_response(f, model, messages, response_text):
    f.write("=" * 60 + "\n")
    f.write(f"Timestamp : {datetime.now().isoformat()}\n")
    f.write(f"Model     : {model}\n")
    f.write("-" * 60 + "\n")
    f.write("User prompt sent:\n")
    user_content = next(m["content"] for m in messages if m["role"] == "user")
    f.write(f"  {user_content}\n")
    f.write("-" * 60 + "\n")
    f.write("Response:\n")
    f.write(response_text + "\n")
    f.write("=" * 60 + "\n\n")


def write_model_error(f, model, error):
    f.write("=" * 60 + "\n")
    f.write(f"Timestamp : {datetime.now().isoformat()}\n")
    f.write(f"Model     : {model}\n")
    f.write(f"ERROR     : {error}\n")
    f.write("=" * 60 + "\n\n")


def main():
    print("Loading schema...")
    schema_content = load_schema()
    system_prompt_with_schema = build_system_prompt(schema_content)
    print(f"Schema loaded ({len(schema_content):,} chars). Starting runs...\n")

    with open(OUTPUT_FILE, "a") as f:
        f.write(f"\n{'#' * 70}\n")
        f.write(f"# Run started: {datetime.now().isoformat()}\n")
        f.write(f"{'#' * 70}\n\n")

    for i, user_prompt in enumerate(PROMPT_VARIANTS, start=1):
        print(f"[Prompt {i}/{len(PROMPT_VARIANTS)}] {user_prompt}")

        with open(OUTPUT_FILE, "a") as f:
            write_prompt_header(f, i, user_prompt)

        for model in MODELS:
            print(f"  -> {model}...", end=" ", flush=True)
            try:
                messages, response_text = run_model(model, user_prompt, system_prompt_with_schema)
                with open(OUTPUT_FILE, "a") as f:
                    write_model_response(f, model, messages, response_text)
                print("done.")
            except Exception as e:
                print(f"ERROR: {e}")
                with open(OUTPUT_FILE, "a") as f:
                    write_model_error(f, model, e)

        print()

    print(f"All done. Results written to {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
