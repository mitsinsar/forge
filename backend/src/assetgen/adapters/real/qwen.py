"""Qwen prompt expansion through an OpenAI-compatible ``/chat/completions``
endpoint (LM Studio, Ollama, llama.cpp server, vLLM...).

Qwen turns the user's short description into an image prompt for the preview
stage. The archetype rules are enforced here, not trusted to the model:
``archetype_override`` always wins, gear and weapons are always previewed on
the biped, and an archetype that is not a template falls back to biped with a
flag the reviewer sees.
"""

from __future__ import annotations

import json
import logging
import re
import urllib.error
import urllib.request

from ...config import Settings
from ...defaults import GEAR_SLOTS, WEAPON_CLASSES

log = logging.getLogger(__name__)

ATTEMPTS = 2  # one retry on an invalid reply


class LLMError(RuntimeError):
    """A readable failure of the prompt stage."""


def _json_part(text: str) -> str:
    """The JSON object in a model's answer (some wrap it in a code fence or a sentence)."""
    start, end = text.find("{"), text.rfind("}")
    return text[start:end + 1] if start != -1 and end > start else text


class _FormatRefused(Exception):
    """The server refused the requested response_format; try the next one."""


SYSTEM_PROMPT = """You write prompts for an image model that makes ONE concept image of a game asset.
The image is later turned into a 3D mesh and rigged, so the rules below matter more than style.

Answer with a single JSON object and nothing else:
{{"archetype": "<one of the archetype ids>", "prompt": "<positive prompt>",
  "negative_prompt": "<negative prompt>", "slot": <gear slot or null>, "weapon_class": <weapon class or null>}}

Archetypes (id: what it is. Rest pose to use):
{archetypes}

Rules for every prompt:
- The subject stands alone, whole and uncropped, in the chosen archetype's rest pose, exactly as written above.
- Neutral plain light-grey background, no scenery, no props, no text.
- Flat, even, shadowless studio lighting; no dramatic light, no rim light, no motion blur.
- The whole subject in frame, centred, facing the camera.
- Describe shapes, materials and colours concretely in one paragraph of comma-separated phrases.
- The negative prompt lists what would break the mesh: extra limbs, cropped parts, dynamic pose,
  busy background, cast shadows, several subjects.
{kind_rules}"""

KIND_RULES = {
    "gear": """This asset is GEAR (an armour or clothing piece for the "{slot}" slot{slot_note}).
- Show the piece alone as an object, with no person, mannequin, body, head or limbs: nobody wears it and
  nothing is inside it. A helmet stands upright facing the camera; a chest piece (with its pauldrons) is
  shown front-on on its own. Gloves, gauntlets, boots and leg armour are drawn as ONE single piece, the
  right one, never a pair (the left one is its mirror image, made later): a glove upright with its
  fingers up, a boot standing on its sole, one leg's armour upright. Only this one piece: no other
  armour, no weapon.
- "archetype" is always "biped". "slot" is one of: {slots}. "weapon_class" is null.""",
    "weapon": """This asset is a WEAPON ({weapon_note}).
- Show the weapon alone, side-on, upright and centred, with no hands, no character and no stand.
- "archetype" is always "biped". "weapon_class" is one of: {classes}. "slot" is null.""",
    "creature": """This asset is a {kind}.
- Pick the archetype whose body plan fits the description best{override_note}.
- Empty-handed: no weapons, shields, staffs, wands, torches, bags or any held item, even when the
  description mentions one (weapons and gear are made as separate assets). Hands open and relaxed.
- Seen straight from the front, unless the archetype's rest pose names another view. Limbs apart from
  the body and from each other, so every limb has a clear gap around it.
- Compact, solid silhouette: no loose chains, floating particles, smoke, fire or effects around the body.
- "slot" and "weapon_class" are null.""",
}

# Always added to the negative prompt of heroes, monsters and bosses: held items and loose
# effects become junk geometry in the mesh, and side or three-quarter views reconstruct worse.
# Always added to the negative prompt of gear and weapons: Hunyuan3D builds whatever is drawn, so a
# person wearing or holding the item becomes part of the mesh.
ITEM_NEGATIVE = ("person, human, mannequin, body, head, face, arms, legs, hands, character wearing it, "
                 "full suit of armor, knight, stand, display stand")
CREATURE_NEGATIVE = ("weapon, sword, shield, staff, holding an object, props, particles, floating debris, "
                     "smoke, aura")
# Bipeds are reconstructed best from the front; other rest poses name their own view.
BIPED_NEGATIVE = "three-quarter view, side view"


MOTION_PROMPT = """You write prompts for a text-to-motion model (HY-Motion) that animates ONE game
character's body casting or performing a skill. The model was trained on motion capture, so plain
prompts come out slow and mild; game skills need clear phases and snappy timing.
Rules:
- English, one paragraph, 25 to 55 words, starting "A person".
- Only the body: what the torso, arms, legs and head do, in order. Say right or left for every
  hand and foot. No effects, magic, objects, weapons, clothing, emotions, camera or scenery:
  a fireball is "thrusts the right palm forward", an arrow is "draws an imaginary bow".
- Four phases: a ready stance; a wind-up (crouches slightly, pulls the arm back, twists the
  torso, raises the arms); the action, with strong timing words (explosively, sharply, quickly,
  forcefully); a short hold of the end pose; then recovers to the ready stance.
- Stay on the spot: no walking or running unless the skill is a dash or a leap.
- If the character holds a two-handed weapon such as a staff, keep both hands close together.
Examples:
"A person stands ready, crouches slightly and pulls the right arm back beside the hip, then
explosively thrusts the right palm forward while stepping forward with the left foot, holds the
pose briefly, and recovers to a ready stance."
"A person raises both arms high above the head, then sharply swings both arms down to the ground
while bending the knees into a deep crouch, holds for a moment, and stands back up."
Also guess how long it takes, 1.5 to 4 seconds (most skills: 2 to 3).
Answer only JSON: {"prompt": "<the motion prompt>", "duration": <seconds>}."""


EFFECT_PROMPT = """You plan a 2D game skill effect (VFX) made of flipbook parts. Each part is drawn as ONE
picture by an image model, alone on a pure black background, then animated by a video model.
Pick the effect's kind: "projectile" (flies from caster to target: needs a "projectile" and an
"impact" part; the projectile is drawn pointing to the right, horizontal), "burst" (happens at a
spot: needs an "impact") or "aura" (stays on a character: needs an "aura"). A "cast" part (a
short glow on the caster's hands) is optional for projectile and burst.
For each part write "picture": what the single frame shows (shape, colours, material, glow; no
characters, hands, ground or scenery, under 40 words). Keep the thing the effect names: an arrow is
drawn as an arrow (shaft, head and fletching) wrapped in its magic, a thrown axe as an axe, a
fireball as a ball of fire. The impact must fit inside its picture with black all around it and "motion": how it moves in place (swirls,
flickers, drips, expands and fades; under 25 words). "blend": "add" for glowing light, fire,
magic, lightning; "mix" for smoke, poison clouds, mud, rocks. "scale": its size next to a
character, 0.25 to 3 (a projectile is about 0.5, an explosion 1 to 2).
Also give "palette": 2 to 4 hex colours, "particles" for the projectile's trail: {"amount" 4-200,
"lifetime" seconds, "speed" pixels/s, "spread" degrees, "gravity" pixels/s^2 (positive falls),
"size" pixels}, and "travel_speed" in pixels/s.
Answer only JSON: {"kind": "...", "palette": [...], "parts": [{"part": "...", "picture": "...",
"motion": "...", "blend": "...", "scale": 1.0}], "particles": {...}, "travel_speed": 600}."""


def build_system_prompt(*, kind: str, archetypes: dict[str, dict], archetype_override: str | None,
                        slot: str | None, weapon_class: str | None, notes: list[str]) -> str:
    lines = "\n".join(f"- {aid}: {spec.get('description', spec.get('name', aid))} Rest pose: {spec['rest_pose']}"
                      for aid, spec in archetypes.items())
    if kind == "gear":
        rules = KIND_RULES["gear"].format(
            slot=slot or "best fitting", slots=", ".join(GEAR_SLOTS),
            slot_note=" chosen by the user" if slot else "; pick the slot from the description")
    elif kind == "weapon":
        rules = KIND_RULES["weapon"].format(
            classes=", ".join(WEAPON_CLASSES),
            weapon_note=f"class {weapon_class}, chosen by the user" if weapon_class
            else "pick the class from the description")
    else:
        override_note = (f'. The user chose the archetype "{archetype_override}": use it and its rest pose'
                         if archetype_override else "")
        rules = KIND_RULES["creature"].format(kind=kind, override_note=override_note)
    text = SYSTEM_PROMPT.format(archetypes=lines, kind_rules=rules)
    kept = [n.strip() for n in notes if n and n.strip()]
    if kept:
        text += ("\n\nThe reviewer rejected earlier images of this asset. Fold every one of these "
                 "notes into the new prompt:\n" + "\n".join(f"- {n}" for n in kept))
    return text


def parse_reply(content: str) -> dict:
    """The JSON object in the model's reply, or ``ValueError``."""
    text = re.sub(r"<think>.*?</think>", "", content or "", flags=re.S).strip()
    fenced = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, flags=re.S)
    if fenced:
        text = fenced.group(1)
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("the reply is not a JSON object")
    if not isinstance(data.get("prompt"), str) or not data["prompt"].strip():
        raise ValueError('the reply has no "prompt" text')
    return data


REPLY_SCHEMA = {
    "type": "object",
    "properties": {
        "archetype": {"type": "string"},
        "prompt": {"type": "string"},
        "negative_prompt": {"type": "string"},
        "slot": {"type": ["string", "null"]},
        "weapon_class": {"type": ["string", "null"]},
    },
    "required": ["archetype", "prompt", "negative_prompt", "slot", "weapon_class"],
    "additionalProperties": False,
}

# JSON modes to ask for, best first. LM Studio accepts only json_schema (it
# rejects json_object with HTTP 400); older servers know only json_object; the
# last resort asks for nothing and relies on the prompt and parse_reply.
RESPONSE_FORMATS: list[dict | None] = [
    {"type": "json_schema", "json_schema": {"name": "asset_prompt", "strict": True, "schema": REPLY_SCHEMA}},
    {"type": "json_object"},
    None,
]


class QwenLLM:
    timeout = 180.0

    def __init__(self, settings: Settings):
        self.url = settings.qwen_url.rstrip("/") + "/chat/completions"
        self.model = settings.qwen_model
        self._format = 0  # index into RESPONSE_FORMATS; moves on when the server refuses one

    def unload(self, model: str | None = None) -> bool:
        """Unloads ``model`` (default: the settings') from LM Studio, so the image model and
        Hunyuan3D get the GPU to themselves; LM Studio loads it again on the next call (about 10 s).
        Needs LM Studio's REST API (``/api/v1/models``); any other server is left alone."""
        wanted = model or self.model
        base = self.url.rsplit("/chat/completions", 1)[0]
        base = base[: -len("/v1")] if base.endswith("/v1") else base
        try:
            with urllib.request.urlopen(base + "/api/v1/models", timeout=10) as response:
                listed = json.load(response).get("models", [])
            instances = [i["id"] for m in listed if m.get("key") == wanted for i in m.get("loaded_instances") or []]
            for instance in instances:
                request = urllib.request.Request(base + "/api/v1/models/unload", method="POST",
                                                 data=json.dumps({"instance_id": instance}).encode("utf-8"),
                                                 headers={"Content-Type": "application/json"})
                urllib.request.urlopen(request, timeout=60).close()
        except (OSError, ValueError, KeyError, TypeError) as exc:
            log.info("could not unload %s from %s: %s", wanted, base, exc)
            return False
        return bool(instances)

    def _post(self, messages: list[dict], model: str | None = None) -> str:
        while True:
            try:
                return self._post_once(messages, RESPONSE_FORMATS[self._format], model)
            except _FormatRefused:
                self._format += 1

    def _post_once(self, messages: list[dict], response_format: dict | None, model: str | None = None) -> str:
        fields = {"model": model or self.model, "messages": messages, "temperature": 0.4}
        if response_format is not None:
            fields["response_format"] = response_format
        body = json.dumps(fields).encode("utf-8")
        request = urllib.request.Request(self.url, data=body, method="POST",
                                         headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:300]
            if exc.code in (400, 422) and response_format is not None:
                raise _FormatRefused() from exc
            raise LLMError(f"Qwen at {self.url} answered HTTP {exc.code}: {detail}") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            reason = getattr(exc, "reason", exc)
            raise LLMError(f"Could not reach Qwen at {self.url} ({reason}). Is the server running and "
                           f"is the model {self.model!r} pulled?") from exc
        except ValueError as exc:
            raise LLMError(f"Qwen at {self.url} did not answer JSON.") from exc
        try:
            return payload["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError(f"Qwen at {self.url} answered without a message.") from exc

    @staticmethod
    def _image_part(path, size: int = 512) -> dict:
        import base64  # noqa: PLC0415
        import io  # noqa: PLC0415

        from PIL import Image  # noqa: PLC0415

        with Image.open(path) as opened:
            small = opened.convert("RGB")
            small.thumbnail((size, size))
            buffer = io.BytesIO()
            small.save(buffer, "JPEG", quality=85)
        data = base64.b64encode(buffer.getvalue()).decode("ascii")
        return {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{data}"}}

    def judge_mesh(self, *, picture, views, description, kind, model=None):
        """Compares renders of a finished 3D model with the picture it was made from. Returns
        ``{"match": 0-10, "problems": [...]}``, or None when the model can't look at images."""
        question = (f"The first image is the picture a 3D model was made from; it should show: {description}. "
                    f"The next {len(views)} images are renders of that 3D model from the front, the side and the "
                    "back. Judge the 3D model only. Answer only JSON: "
                    '{"match": <0-10: how well the 3D model shows the same thing as the picture, with all its main '
                    'parts, the right shape and colours; 10 is the same; take off for missing or extra parts, a '
                    'ground or floor plate under it, melted or broken areas, holes, a wrong thing>, '
                    '"problems": [<short phrases: what is wrong, or nothing>]}')
        content = [{"type": "text", "text": question}, self._image_part(picture),
                   *(self._image_part(v, 384) for v in views)]
        try:
            answer = json.loads(_json_part(self._post_once([{"role": "user", "content": content}], None, model)))
        except (LLMError, ValueError):
            return None
        try:
            match = max(0, min(10, int(round(float(answer.get("match"))))))
        except (TypeError, ValueError):
            return None
        return {"match": match, "problems": [str(p) for p in (answer.get("problems") or []) if str(p).strip()][:5]}

    def judge_motions(self, *, sheets, description, prompt, model=None):
        """Compares the versions of a skill animation from their stick-figure sheets (``motion_sheet``):
        ``[{"match": 0-10, "problems": [...]}]`` in the sheets' order, or None when it can't look."""
        question = (f"These {len(sheets)} images are {len(sheets)} versions of the same game character's skill "
                    "animation, each drawn as a stick figure at six moments (time in the corner): the top row from "
                    "the front (the figure faces you, so its right side is on YOUR left), the bottom row from its "
                    "right side (it faces right). Its left limbs are blue, its right limbs red. "
                    f"The skill: {description}. The animation was asked for as: {prompt} "
                    "Compare the versions and score each as a game animation: does the body do what the skill "
                    "needs, with the right limbs (red is right), a clear wind-up, a strong readable action and a "
                    "return to a ready stance? Take off for the wrong arm or leg, a weak or unclear action, walking "
                    "or drifting away, turning away, falling over, broken-looking limbs. Give different scores "
                    "where they differ. Answer only JSON: "
                    '{"versions": [{"version": <1-based>, "match": <0-10>, "problems": [<short phrases>]}]}')
        content = [{"type": "text", "text": question}]
        for number, sheet in enumerate(sheets, start=1):
            content += [{"type": "text", "text": f"Version {number}:"}, self._image_part(sheet, 768)]
        reply = self._post_once([{"role": "user", "content": content}], None, model)  # LLMError: the caller retries
        try:
            answer = json.loads(_json_part(reply))
        except (LLMError, ValueError):
            return None
        verdicts: list = [None] * len(sheets)
        for item in answer.get("versions") or []:
            try:
                number = int(item.get("version"))
                match = max(0, min(10, int(round(float(item.get("match"))))))
            except (TypeError, ValueError, AttributeError):
                continue
            if 1 <= number <= len(sheets):
                verdicts[number - 1] = {"match": match, "problems": [str(x) for x in (item.get("problems") or [])
                                                                     if str(x).strip()][:5]}
        return verdicts if any(verdicts) else None

    def inspect(self, *, image, kind, description=None, model=None):
        """Asks the (vision) model about a candidate. Returns None when it can't look at images."""
        subject = {"gear": "an armour or clothing piece shown alone", "weapon": "a weapon shown alone"}.get(
            kind, "a game character")
        question = (f"This image should show {subject}, whole, on a plain background. Answer only JSON: "
                    '{"held_item": <true if a character holds or carries a weapon, shield, staff, bag or any other '
                    'object, else false>, "held_item_name": <what it holds, or null>, "figures": <how many '
                    'separate figures or objects are shown>, "whole_body": <true if nothing is cut off by the '
                    'image edges>, "person": <true if a person, mannequin, body, head or limbs are shown, even '
                    'only wearing or holding something, else false>'
                    + (f', "match": <0-10: how well the image shows this description: {description}; the right '
                       'thing, its parts, materials and colours; 10 is exact>, "problems": [<short phrases: what is '
                       'wrong or missing, or nothing>]' if description else "") + '}')
        messages = [{"role": "user", "content": [{"type": "text", "text": question}, self._image_part(image)]}]
        try:
            answer = json.loads(_json_part(self._post_once(messages, None, model)))
        except (LLMError, ValueError):
            return None
        found = {"held_item": bool(answer.get("held_item")), "held_item_name": answer.get("held_item_name"),
                 "figures": int(answer.get("figures") or 1), "whole_body": bool(answer.get("whole_body", True)),
                 "person": bool(answer.get("person"))}
        if description:
            try:
                found["match"] = max(0, min(10, int(round(float(answer.get("match"))))))
            except (TypeError, ValueError):
                pass
            found["problems"] = [str(p) for p in (answer.get("problems") or []) if str(p).strip()][:5]
        return found

    def inspect_effect(self, *, image, description, model=None):
        """Asks the vision model about a skill effect's picture: ``{"count", "match", "problems"}``, or None."""
        question = ("This image should show ONE game VFX sprite alone on black: " + description + ". Answer only "
                    'JSON: {"count": <how many separate objects are shown>, "match": <0-10: how well it shows exactly '
                    'that: the right thing, one of it, its shape and colours (ignore which way it faces); 10 is '
                    'exact>, "points": <"left", "right", "up", "down" or "none": the way its front or head points>, '
                    '"problems": [<short phrases: what is wrong or missing, or nothing; not its direction>]}')
        messages = [{"role": "user", "content": [{"type": "text", "text": question}, self._image_part(image)]}]
        try:
            answer = json.loads(_json_part(self._post_once(messages, None, model)))
            found = {"count": int(answer.get("count") or 1),
                     "match": max(0, min(10, int(round(float(answer.get("match")))))),
                     "points": str(answer.get("points") or "none").lower()}
        except (LLMError, ValueError, TypeError):
            return None
        found["problems"] = [str(p) for p in (answer.get("problems") or []) if str(p).strip()][:5]
        return found

    def vary(self, *, description, kind, count, model=None):
        text = (f"Write {count} different variations of this {kind} description for a game asset generator. "
                "Same kind of subject, but vary looks, colours, materials and details; one or two short sentences "
                f'each. Answer only JSON: {{"descriptions": [<{count} strings>]}}.\n\nDescription: {description}')
        answer = json.loads(_json_part(self._post_once([{"role": "user", "content": text}], None, model)))
        variants = [str(d).strip() for d in answer.get("descriptions", []) if str(d).strip()]
        if len(variants) < count:
            raise LLMError(f"Qwen wrote {len(variants)} variations instead of {count}.")
        return variants[:count]

    def motion_prompt(self, *, description, notes=(), model=None) -> dict:
        """A skill's description as a prompt for the motion model (HY-Motion): ``{"prompt", "duration"}``."""
        text = MOTION_PROMPT + f"\n\nSkill: {description.strip()}"
        if notes:
            text += "\nChanges asked for: " + "; ".join(n for n in notes if n.strip())
        answer = json.loads(_json_part(self._post_once([{"role": "user", "content": text}], None, model)))
        prompt = " ".join(str(answer.get("prompt") or "").split())
        if not prompt:
            raise LLMError("The language model wrote no motion prompt.")
        try:
            duration = float(answer.get("duration") or 2.5)
        except (TypeError, ValueError):
            duration = 2.5
        return {"prompt": prompt, "duration": round(max(1.0, min(6.0, duration)), 1)}

    def effect_plan(self, *, description, notes=(), model=None) -> dict:
        """A skill effect's parts as prompts (see ``effects.normalize_plan``, which completes it)."""
        text = EFFECT_PROMPT + f"\n\nEffect: {description.strip()}"
        if notes:
            text += "\nChanges asked for: " + "; ".join(n for n in notes if n.strip())
        return json.loads(_json_part(self._post_once([{"role": "user", "content": text}], None, model)))

    def expand(self, *, description, kind, archetypes, archetype_override, slot, weapon_class, notes, model=None):
        system = build_system_prompt(kind=kind, archetypes=archetypes, archetype_override=archetype_override,
                                     slot=slot, weapon_class=weapon_class, notes=notes)
        messages = [{"role": "system", "content": system},
                    {"role": "user", "content": f"Asset kind: {kind}\nDescription: {description.strip()}"}]
        problem = ""
        for attempt in range(ATTEMPTS):
            content = self._post(messages, model)
            try:
                data = parse_reply(content)
                break
            except ValueError as exc:
                problem = str(exc) if not isinstance(exc, json.JSONDecodeError) else f"invalid JSON ({exc.msg})"
                messages = messages + [
                    {"role": "assistant", "content": content or ""},
                    {"role": "user", "content": f"That reply was not usable: {problem}. Answer again with only "
                                                "the JSON object described in the instructions."}]
        else:
            raise LLMError(f"Qwen did not give a usable prompt after {ATTEMPTS} tries: {problem}.")
        return self._settle(data, kind=kind, archetypes=archetypes, archetype_override=archetype_override,
                            slot=slot, weapon_class=weapon_class)

    @staticmethod
    def _settle(data: dict, *, kind, archetypes, archetype_override, slot, weapon_class) -> dict:
        flags: list[str] = []
        answered = data.get("archetype")
        if kind in ("gear", "weapon"):
            archetype = "biped"  # items are previewed on the biped mannequin
        elif archetype_override:
            archetype = archetype_override
        elif isinstance(answered, str) and answered in archetypes:
            archetype = answered
        else:
            archetype = "biped"
            flags.append(f"Qwen chose the archetype {answered!r}, which is not a template; biped is used instead.")
        if kind == "gear":
            slot = slot or (data.get("slot") if data.get("slot") in GEAR_SLOTS else "chest")
        if kind == "weapon":
            weapon_class = weapon_class or (data.get("weapon_class") if data.get("weapon_class") in WEAPON_CLASSES
                                            else "one_handed")
        negative = data.get("negative_prompt")
        negative = negative.strip() if isinstance(negative, str) else ""
        if kind in ("gear", "weapon"):
            negative = ", ".join(p for p in (negative, ITEM_NEGATIVE) if p)
        if kind not in ("gear", "weapon"):
            extra = (CREATURE_NEGATIVE, BIPED_NEGATIVE if archetype == "biped" else "")
            negative = ", ".join(p for p in (negative, *extra) if p)
        return {
            "archetype": archetype,
            "prompt": data["prompt"].strip(),
            "negative_prompt": negative,
            "slot": slot if kind == "gear" else None,
            "weapon_class": weapon_class if kind == "weapon" else None,
            "flags": flags,
        }
