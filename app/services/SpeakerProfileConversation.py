"""
Conversation AI: user-facing assistant messages for chatbot init.

Only generate_chatbot_welcome_message is active. Other helpers and generators
are commented out below (used by step-based onboarding / verify-step).
"""

def generate_chatbot_welcome_message() -> str:
    """
    Welcome for POST /init-chatbot (same flow as POST /chat first turn).
    Exact copy for the product; MUST include Human Driven AI and SpeakerPitcher™.
    """
    return (
        "Hi! Welcome to Human Driven AI's SpeakerPitcher™ Agent."
        "To start your profiles, please provide your name as you would like it to appear professionally "
        "(e.g., Jane Doe, MBA, PMP), followed by your title and company."
    )




# """
# Conversation AI: generates user-facing assistant messages.
## This module MUST NOT perform validation decisions. It only turns:
# - (step, normalized_answer, next_step) into a transition message
# - (step, user_answer, reason_code, retry_count) into a recovery message
# """
## from __future__ import annotations
## import os
# import hashlib
# from typing import Any, Dict, List, Optional, Union
## from openai import OpenAI
## from app.config.speaker_profile_steps import StepDefinition, get_step_by_name
### def _allowed_display(allowed: Optional[List[Any]]) -> List[str]:
#     """Convert allowed_values to list of display strings (for recovery messages). Handles list of str or list of topic objects."""
#     if not allowed:
#         return []
#     return [
#         (x.get("name") or x.get("slug") or "") if isinstance(x, dict) else str(x)
#         for x in allowed
#     ]
### def _stable_seed(*parts: str) -> int:
#     h = hashlib.sha256("||".join(parts).encode("utf-8")).hexdigest()
#     return int(h[:8], 16)
### def _fallback_transition(
#     step_name: str,
#     normalized_answer: Any,
#     next_step: Optional[Dict[str, Any]],
#     is_last_step: bool = False,
# ) -> str:
#     if is_last_step:
#         return "You've completed your speaker profile—thanks for sharing!"
#     if step_name == "full_name":
#         name = str(normalized_answer or "").strip()
#         first = name.split()[0] if name else ""
#         next_q = next_step.get("question") if next_step else "What are some of the topics you want to cover in your speaking opportunities?"
#         return f"Great, it's nice to meet you, {first}! {next_q}" if first else f"Great, it's nice to meet you! {next_q}"
#     if next_step and next_step.get("question"):
#         return f"Got it. {next_step['question']}"
#     return "Got it. What's next?"
### def _fallback_recovery(
#     step: StepDefinition,
#     reason_code: str,
#     retry_count: int,
#     allowed_values: Optional[List[Any]] = None,
# ) -> str:
#     q = step.question
#     raw_allowed = allowed_values if allowed_values is not None else (step.allowed_values or [])
#     allowed = _allowed_display(raw_allowed) if raw_allowed and isinstance(raw_allowed[0], dict) else (raw_allowed if isinstance(raw_allowed, list) else [])
##     variants = []
#     if reason_code in ("EMPTY", "REQUIRED"):
#         if step.step_name == "topics":
#             variants = [
#                 "Topics help us match you to opportunities. Could you pick one or more from the list?",
#                 "Picking a topic helps your profile—choose one or more from the list when you're ready!",
#             ]
#         elif step.step_name == "speaking_formats":
#             variants = [
#                 "Speaking formats help event organizers find you. Could you pick one or more from the list?",
#                 "Picking your formats helps your profile—choose from the list when you're ready!",
#             ]
#         elif step.step_name == "delivery_mode":
#             variants = [
#                 "How you deliver helps us match you to the right events. Could you pick one or more from the list?",
#                 "Picking delivery mode helps your profile—choose from the list when you're ready!",
#             ]
#         elif step.step_name == "talk_description":
#             variants = [
#                 "A short description of your talk helps us match you to the right events. Could you share a bit about your talk or expertise?",
#                 "Describing your talk helps your profile—share a few sentences when you're ready!",
#             ]
#         elif step.step_name == "key_takeaways":
#             variants = [
#                 "Key takeaways help organizers see what audiences will leave with. Could you share a few highlights from your talks?",
#                 "Sharing main takeaways strengthens your profile—a few bullet points or sentences work great!",
#             ]
#         elif step.step_name == "testimonial":
#             variants = [
#                 "Testimonials from past speaking help organizers trust your impact. Could you share any you've received?",
#                 "If you have testimonials from past events, sharing them strengthens your profile—whenever you're ready!",
#             ]
#         elif step.step_name == "target_audiences":
#             variants = [
#                 "Target audiences help us match you to the right events. Could you pick one or more from the list?",
#                 "Picking your audience helps your profile—choose one or more from the list when you're ready!",
#             ]
#         else:
#             variants = [
#                 f"I didn't catch that—could you share? {q}",
#                 f"Could you share that with me? {q}",
#                 f"Just to make sure I understand—{q}",
#             ]
#     elif reason_code == "MISSING_PROFILE_ID":
#         variants = [
#             "Let's start from the beginning—what is your full name?",
#             "I don't have your profile yet. Could you tell me your full name first?",
#         ]
#     elif reason_code == "REFUSAL":
#         # Calm, friendly reply when user declines. For topics, explain why it matters for their profile without demanding.
#         if step.step_name == "full_name":
#             variants = [
#                 "No problem. When you're ready, share your full name and we can continue.",
#                 "That's okay. Whenever you're ready, share your full name to continue.",
#             ]
#         elif step.step_name == "email":
#             variants = [
#                 "No problem. When you're ready, share your email and we can continue.",
#                 "That's okay. Whenever you're ready, share your email to continue.",
#             ]
#         elif step.step_name == "topics":
#             variants = [
#                 "No pressure! Topics help us match you to opportunities. Whenever you're ready, pick one or more from the list.",
#                 "That's okay! If you don't see yours, pick the closest match—we can refine later. Ready when you are!",
#             ]
#         elif step.step_name == "speaking_formats":
#             variants = [
#                 "No pressure! Speaking formats help event organizers find you. Whenever you're ready, pick one or more from the list.",
#                 "That's okay! Pick one or more formats from the list when you're ready—it helps your profile.",
#             ]
#         elif step.step_name == "delivery_mode":
#             variants = [
#                 "No pressure! How you deliver helps us match you to events. Whenever you're ready, pick one or more from the list.",
#                 "That's okay! Pick your delivery option(s) from the list when you're ready—it helps your profile.",
#             ]
#         elif step.step_name == "target_audiences":
#             variants = [
#                 "No pressure! Target audiences help us match you to the right events. Whenever you're ready, pick one or more from the list.",
#                 "That's okay! Picking your audience helps your profile—choose from the list when you're ready!",
#             ]
#         elif step.step_name == "linkedin_url":
#             variants = [
#                 "No problem. You can skip this step—we'll move on.",
#                 "That's okay. We'll continue without it.",
#             ]
#         elif step.step_name == "past_speaking_examples":
#             variants = [
#                 "No problem. You can skip this step—we'll move on.",
#                 "That's okay. We'll continue without it.",
#             ]
#         elif step.step_name == "video_links":
#             variants = [
#                 "No problem. You can skip this step—we'll move on.",
#                 "That's okay. We'll continue without it.",
#             ]
#         elif step.step_name == "talk_description":
#             variants = [
#                 "No problem. When you're ready, share a bit about your talk and we can continue.",
#                 "That's okay. Whenever you're ready, describe your talk or expertise and we'll move on.",
#             ]
#         elif step.step_name == "key_takeaways":
#             variants = [
#                 "No problem. You can skip this step—we'll move on.",
#                 "That's okay. We'll continue without it.",
#             ]
#         elif step.step_name == "testimonial":
#             variants = [
#                 "No problem. You can skip this step—we'll move on.",
#                 "That's okay. We'll continue without it.",
#             ]
#         else:
#             variants = [
#                 "No problem. When you're ready, we can continue.",
#             ]
#     elif reason_code == "INVALID_FULL_NAME":
#         variants = [
#             "That doesn't quite look like a full name. Could you share your real full name (first and last)?",
#             "Please enter your full name, e.g. first and last name.",
#         ]
#     elif reason_code == "INVALID_EMAIL":
#         variants = [
#             "That doesn't look like a valid email address. Could you double-check and try again?",
#             "Please enter a valid email address (e.g. name@example.com).",
#         ]
#     elif reason_code in ("INVALID_URL",):
#         if step.step_name == "linkedin_url":
#             variants = [
#                 "Those links don't look like supported professional social URLs (LinkedIn, Facebook, X, or Instagram). Paste full https links, or you can skip.",
#                 "Please share valid LinkedIn, Facebook, X, or Instagram profile URLs—or skip and add them later from your profile.",
#             ]
#         else:
#             variants = [
#                 "That link doesn't look quite right. Could you paste the full URL (including https://)?",
#                 "Could you share a valid URL? It should start with https://",
#                 "I’m having trouble with that link—can you paste the full URL?",
#             ]
#     elif reason_code in ("ENUM_NO_MATCH", "ENUM_INVALID"):
#         if step.step_name == "topics" and allowed:
#             variants = [
#                 "Topics help us match you to opportunities. Could you pick one or more from the list?",
#                 "Picking a topic helps your profile—choose one or more from the list when you're ready!",
#             ]
#         elif step.step_name == "speaking_formats" and allowed:
#             variants = [
#                 "Speaking formats help event organizers find you. Could you pick one or more from the list?",
#                 "Picking your formats helps your profile—choose from the list when you're ready!",
#             ]
#         elif step.step_name == "delivery_mode" and allowed:
#             variants = [
#                 "How you deliver helps us match you to the right events. Could you pick one or more from the list?",
#                 "Picking delivery mode helps your profile—choose from the list when you're ready!",
#             ]
#         elif step.step_name == "target_audiences" and allowed:
#             variants = [
#                 "Target audiences help us match you to the right events. Could you pick one or more from the list?",
#                 "Picking your audience helps your profile—choose from the list when you're ready!",
#             ]
#         elif allowed:
#             display_str = ", ".join(str(a) for a in allowed)
#             variants = [
#                 f"I didn't quite catch that—could you choose from: {display_str}?",
#                 f"Which option fits best? Pick from: {display_str}.",
#                 f"To keep things consistent, please choose from: {display_str}.",
#             ]
#         else:
#             variants = [
#                 "I didn't quite catch that—could you try again?",
#                 "Could you rephrase that for me?",
#                 "Can you share that again in a bit more detail?",
#             ]
#     elif reason_code == "GIBBERISH" and step.step_name == "video_links":
#         variants = [
#             "That doesn't look like a video link. Paste a YouTube URL, or you can skip this step.",
#             "We need a speaking video link (YouTube) or you can skip. Your choice!",
#         ]
#     elif reason_code in ("GIBBERISH", "UNRELATED") and step.step_name in ("talk_description", "key_takeaways", "testimonial"):
#         if step.step_name == "talk_description":
#             variants = [
#                 "A short description helps us match you to the right events. Could you share a bit about your talk or expertise?",
#                 "Describing your talk helps your profile—share a few sentences when you're ready!",
#             ]
#         elif step.step_name == "key_takeaways":
#             variants = [
#                 "That doesn't look like key takeaways from your talks yet. Could you share specific points or lessons audiences gain?",
#                 "Think bullet-style outcomes from your sessions—happy to capture them when you share!",
#             ]
#         else:
#             variants = [
#                 "That doesn't read like a testimonial (quote or feedback about your speaking). Could you paste a real testimonial if you have one?",
#                 "Organizers love short quotes or feedback from past events—share a genuine testimonial when you can!",
#             ]
#     else:
#         # IRRELEVANT / GIBBERISH / SPAM / LOW_EFFORT / UNKNOWN
#         variants = [
#             f"I’m not totally sure I understood—could you answer this more directly? {q}",
#             f"Could you share a bit more detail so I can capture it accurately? {q}",
#             f"Help me understand—what would you say for this? {q}",
#         ]
##     idx = (retry_count or 0) % max(len(variants), 1)
#     msg = variants[idx]
##     # Escalate help after repeated failures: examples; for enum optionally list allowed_values
#     if (retry_count or 0) > 1 and allowed:
#         if reason_code in ("ENUM_NO_MATCH", "ENUM_INVALID"):
#             msg = f"{msg} Allowed options: {', '.join(str(a) for a in allowed)}."
#         else:
#             msg = f"{msg} For example: {allowed[0]}."
#     return msg
##