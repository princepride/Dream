# Add at the VERY TOP of the script
import os
os.environ['CUDA_LAUNCH_BLOCKING'] = '1'
print(f"CUDA_LAUNCH_BLOCKING set to: {os.environ.get('CUDA_LAUNCH_BLOCKING')}")  # Verify

import torch
import time
import gradio as gr
from transformers import AutoModel, AutoTokenizer
import copy
import traceback

# --- Model Loading ---
model_path = "Dream-org/Dream-v0-Instruct-7B"
device = 'cuda' if torch.cuda.is_available() else 'cpu'
print(f"Using device: {device}")

try:
    # --- Use float32 for stability ---
    print("Loading model with float32...")
    dtype = torch.float32
    model = AutoModel.from_pretrained(model_path, torch_dtype=dtype, trust_remote_code=True)
    print(f"Model loaded successfully with {dtype}.")
except Exception as e:
    print(f"Fatal Error loading model: {e}")
    print(traceback.format_exc())
    exit()

# --- Tokenizer Loading ---
try:
    tokenizer = AutoTokenizer.from_pretrained(model_path, trust_remote_code=True)
    print("Tokenizer loaded successfully.")
except Exception as e:
    print(f"Fatal Error loading tokenizer: {e}")
    print(traceback.format_exc())
    exit()

# Define mask token parameters for visualization
mask_token_id = tokenizer.mask_token_id if tokenizer.mask_token_id is not None else -100
mask_token_str = "[MASK]"

# --- Move model to device ---
try:
    model = model.to(device).eval()
    print(f"Model moved to {device} and set to eval mode.")
except Exception as e:
    print(f"Fatal Error moving model to device: {e}")
    print(traceback.format_exc())
    exit()

# --- Helper Functions ---
def format_gradio_history_to_messages(history):
    messages = []
    if not history:
        return messages
    for pair in history:
        user_msg, assistant_msg = pair
        messages.append({"role": "user", "content": user_msg})
        if assistant_msg is not None:
            messages.append({"role": "assistant", "content": str(assistant_msg)})
    return messages

def add_user_message_to_gradio_history(history, message):
    if not history:
        history = []
    return history + [[message, None]]

# --- Main Generation Function with Visualization ---
def dream_generate_with_visualization(history, max_new_tokens, steps, temperature, top_p, top_k, delay):
    print("\n--- Starting dream_generate_with_visualization ---")
    print(f"Parameters: max_new_tokens={max_new_tokens}, steps={steps}, temperature={temperature}, top_p={top_p}, top_k={top_k}, delay={delay}")

    messages_for_model = format_gradio_history_to_messages(history)

    try:
        inputs = tokenizer.apply_chat_template(messages_for_model, return_tensors="pt", return_dict=True, add_generation_prompt=True)
        input_ids = inputs.input_ids.to(device)
        attention_mask = inputs.attention_mask.to(device)
        prompt_length = input_ids.shape[1]
        print(f"Prompt length: {prompt_length}, input_ids device: {input_ids.device}")
    except Exception as e:
        print(f"Error during input tokenization/processing: {e}")
        error_message = f"Input processing error: {e}"
        current_history = copy.deepcopy(history)
        if current_history:
            current_history[-1][1] = f"Error: {error_message}"
        else:
            current_history = [["System", f"Error: {error_message}"]]
        yield format_gradio_history_to_messages(current_history), error_message, current_history
        return

    visualization_token_states = []
    hook_call_count = 0

    # Hook to save intermediate token states
    def my_generation_tokens_hook(step, x, logits):
        nonlocal hook_call_count
        hook_call_count += 1
        visualization_token_states.append(x[0].clone().cpu())
        return x

    effective_top_k = top_k if top_k > 0 else None

    print("Calling model.diffusion_generate...")
    start_time = time.time()
    output = None
    try:
        print(f"Model device before generate: {next(model.parameters()).device}")
        output = model.diffusion_generate(
            input_ids,
            attention_mask=attention_mask,
            max_new_tokens=max_new_tokens,
            output_history=False,
            return_dict_in_generate=True,
            steps=steps,
            temperature=temperature,
            top_p=top_p,
            top_k=effective_top_k,
            generation_tokens_hook_func=my_generation_tokens_hook
        )
        end_time = time.time()
        print(f"model.diffusion_generate finished in {end_time - start_time:.2f} seconds.")
    except Exception as e:
        print(f"Error during diffusion_generate: {e}")
        tb_str = traceback.format_exc()
        print(f"Traceback:\n{tb_str}")
        error_detail = str(e)
        if "illegal memory access" in error_detail.lower():
            error_message = (
                "Model generation error (CUDA Illegal Memory Access).\n"
                "Possible reasons:\n"
                "- 'Diffusion Steps'/'Max New Tokens' might be too high.\n"
                "- Model code and current environment (driver/CUDA/PyTorch) are incompatible.\n"
                "- Using float32 might cause issues if switching from bfloat16/float16.\n"
                "Try lowering 'Diffusion Steps' and 'Max New Tokens', updating your drivers, or checking model issues."
            )
        else:
            error_message = f"Unknown error during model generation: {e}"

        current_history = copy.deepcopy(history)
        if current_history:
            current_history[-1][1] = f"Error: {error_message}"
        else:
            current_history = [["System", f"Error: {error_message}"]]
        yield format_gradio_history_to_messages(current_history), error_message, current_history
        print("--- Exiting dream_generate_with_visualization due to error ---")
        return

    # --- Intermediate Yield Loop with Colored Boxes ---
    intermediate_history = copy.deepcopy(history)
    num_states_to_process = len(visualization_token_states)
    print(f"Starting intermediate yield loop for {num_states_to_process - 1} states...")

    # Initialize previous tokens for the generated part (all masked)
    first_state = visualization_token_states[0]
    gen_length = first_state.shape[0] - prompt_length
    previous_tokens = [mask_token_id] * gen_length

    # Iterate over each intermediate state (skip the first state which is all masked)
    for i, state_tensor in enumerate(visualization_token_states[1:]):
        current_state_tensor = state_tensor[prompt_length:]
        current_tokens = current_state_tensor.tolist()
        colored_tokens = []
        for idx, token_id in enumerate(current_tokens):
            if token_id == mask_token_id:
                colored_tokens.append((mask_token_str, "#444444"))
            else:
                if previous_tokens[idx] == mask_token_id:
                    token_str = tokenizer.decode([token_id], skip_special_tokens=True)
                    colored_tokens.append((token_str, "#66CC66"))
                else:
                    token_str = tokenizer.decode([token_id], skip_special_tokens=True)
                    colored_tokens.append((token_str, "#6699CC"))
        previous_tokens = current_tokens
        intermediate_history[-1][1] = f"⏳ Step {i+1}/{num_states_to_process - 1}"
        messages_for_chatbot_update = format_gradio_history_to_messages(intermediate_history)
        time.sleep(delay)
        yield messages_for_chatbot_update, colored_tokens, history

    print("Processing final result...")
    try:
        final_tokens_tensor = output.sequences[0][prompt_length:]
        final_tokens_list = final_tokens_tensor.tolist()
        colored_final = []
        for token_id in final_tokens_list:
            if token_id == mask_token_id:
                colored_final.append((mask_token_str, "#444444"))
            else:
                token_str = tokenizer.decode([token_id], skip_special_tokens=True)
                colored_final.append((token_str, "#6699CC"))
        final_text = tokenizer.decode(final_tokens_list, skip_special_tokens=True, clean_up_tokenization_spaces=True).strip()
        history[-1][1] = final_text
        final_messages_for_chatbot = format_gradio_history_to_messages(history)
        print("Yielding final result.")
        yield final_messages_for_chatbot, colored_final, history
    except Exception as e:
        print(f"Error processing final output: {e}")
        error_message = f"Error processing final output: {e}"
        current_history = copy.deepcopy(history)
        if current_history:
            current_history[-1][1] = f"Error processing output: {error_message}"
        else:
            current_history = [["System", f"Error processing output: {error_message}"]]
        yield format_gradio_history_to_messages(current_history), error_message, current_history

    print("--- Exiting dream_generate_with_visualization normally ---")

# --- Bot Response Generator Wrapper ---
def bot_response_generator(history, max_new_tokens, steps, temperature, top_p, top_k, delay):
    if not history or history[-1][1] is not None:
        print("Skipping bot response: No history or last message already has a response.")
        yield format_gradio_history_to_messages(history), "", history
        return
    yield from dream_generate_with_visualization(history, max_new_tokens, steps, temperature, top_p, top_k, delay)

# --- User Message Submission Handler ---
def user_message_submitted(message, history):
    if not message or not message.strip():
        return history, format_gradio_history_to_messages(history), ""
    new_history = add_user_message_to_gradio_history(history, message)
    messages_for_chatbot = format_gradio_history_to_messages(new_history)
    return new_history, messages_for_chatbot, ""

# --- Gradio UI ---
css = """
/* Make chatbot text selectable */
.gradio-container .prose ::selection { background-color: #ACE6FF; }
.gradio-container .prose ::-moz-selection { background-color: #ACE6FF; }
"""

with gr.Blocks(css=css, theme=gr.themes.Soft()) as demo:
    gr.Markdown("# Dream Diffusion Model Demo (Text-to-Text)")
    gr.Markdown("Interact with the **Dream-v0-Instruct-7B** model in a multi-turn conversation and watch the diffusion process.")
    gr.Markdown("Model link: [Dream-org/Dream-v0-Instruct-7B](https://huggingface.co/Dream-org/Dream-v0-Instruct-7B)")

    chat_history_state = gr.State([])

    with gr.Row():
        with gr.Column(scale=3):
            chatbot_display = gr.Chatbot(label="Chat", bubble_full_width=False, height=600, type="messages")
            with gr.Group():
                with gr.Row():
                    user_input_textbox = gr.Textbox(label="Your Message", placeholder="Type your message here...", scale=4, show_label=False, container=False)
                    send_button = gr.Button("Send", scale=1, variant="primary")

        with gr.Column(scale=2):
            vis_output_display = gr.HighlightedText(label="Diffusion Process Visualization", show_legend=True, combine_adjacent=False)

    with gr.Accordion("Generation Parameters", open=False):
        max_new_tokens_slider = gr.Slider(16, 512, value=128, step=16, label="Max New Tokens")
        steps_slider = gr.Slider(8, 512, value=128, step=8, label="Diffusion Steps")
        temperature_slider = gr.Slider(0.0, 1.0, value=0.0, step=0.05, label="Temperature (0 = deterministic)")
        top_p_slider = gr.Slider(0.0, 1.0, value=0.95, step=0.05, label="Top-p (0 = disabled)")
        top_k_slider = gr.Slider(0, 100, value=0, step=1, label="Top-k (0 = disabled)")
        delay_slider = gr.Slider(0.0, 0.5, value=0.02, step=0.01, label="Visualization Delay (seconds)")

    clear_button = gr.Button("Clear Chat")

    def clear_conversation():
        return [], [], "", ""

    clear_button.click(
        fn=clear_conversation,
        inputs=[],
        outputs=[chat_history_state, chatbot_display, user_input_textbox, vis_output_display],
        queue=False
    )

    generation_params = [max_new_tokens_slider, steps_slider, temperature_slider, top_p_slider, top_k_slider, delay_slider]

    submit_event_args = dict(
        fn=user_message_submitted,
        inputs=[user_input_textbox, chat_history_state],
        outputs=[chat_history_state, chatbot_display, user_input_textbox],
    )

    bot_response_event_args = dict(
        fn=bot_response_generator,
        inputs=[chat_history_state] + generation_params,
        outputs=[chatbot_display, vis_output_display, chat_history_state]
    )

    submit_action = user_input_textbox.submit(**submit_event_args)
    submit_action.then(lambda: "", inputs=None, outputs=[vis_output_display])
    submit_action.then(**bot_response_event_args)

    send_action = send_button.click(**submit_event_args)
    send_action.then(lambda: "", inputs=None, outputs=[vis_output_display])
    send_action.then(**bot_response_event_args)

if __name__ == "__main__":
    demo.queue(max_size=10, default_concurrency_limit=1).launch(share=True, debug=True)
