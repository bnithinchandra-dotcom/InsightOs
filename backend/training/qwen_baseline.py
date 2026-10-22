import torch
from transformers import AutoTokenizer, AutoModelForCausalLM

MODEL_PATH = "backend/artifacts/qwen3-0.6b-hf-c1899de"

print("Loading tokenizer...")
tokenizer = AutoTokenizer.from_pretrained(
    MODEL_PATH,
    local_files_only=True,
)

print("Loading model on CPU...")
model = AutoModelForCausalLM.from_pretrained(
    MODEL_PATH,
    local_files_only=True,
    dtype=torch.bfloat16,
    device_map="cpu",
)
model.eval()

messages = [
    {
        "role": "user",
        "content": (
            "Explain the difference between revenue and profit "
            "in exactly two short sentences."
        ),
    }
]

inputs = tokenizer.apply_chat_template(
   messages,
    tokenize=True,
    add_generation_prompt=True,
    return_dict=True,
    return_tensors="pt",
    enable_thinking=False,
)

input_ids = inputs["input_ids"]
print("Input tokens:", input_ids.shape[1])

print("Generating response...")
with torch.inference_mode():
    outputs = model.generate(
        **inputs,
        max_new_tokens=96,
        do_sample=False,
        eos_token_id=tokenizer.eos_token_id,
    )

new_tokens = outputs[0, input_ids.shape[1]:]

print("\n=== QWEN CHAT BASELINE ===")
print(tokenizer.decode(new_tokens, skip_special_tokens=True))
