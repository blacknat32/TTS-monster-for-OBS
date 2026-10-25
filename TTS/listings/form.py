import re
from pathlib import Path

from django import forms
from django.utils.html import format_html, format_html_join

from TTS.chatbot import VOICE_ID


SOUND_BITE_DIRECTORY = Path(__file__).resolve().parents[1] / "media" / "Sound_Bites"
SOUND_BITE_EXTENSIONS = {".aac", ".flac", ".m4a", ".mp3", ".ogg", ".wav"}
SOUND_BITE_TAG_PATTERN = re.compile(r"\{([^{}]+)\}")
VOICE_CHOICES = [
    (VOICE_ID, "SpongeBob"),
    ("c4ad44ae-8da9-4375-90c0-55a1e6f1fbc6", "Adam"),
    ("26c9f68f-a1bd-4871-a76c-6eee020c5b07", "Albus"),
    ("1aa9d694-1da5-4bdb-8e07-6392ec526c2f", "ASMR"),
    ("49826b97-091a-4821-a978-15692387647a", "Biden"),
    ("0993f688-6719-4cf6-9769-fee7b77b1df5", "Brian"),
    ("4b6941d0-0d79-424a-8c66-10c2942293dc", "Dagoth"),
    ("f66d7026-41af-4ff6-8f17-a748559eb13e", "Isabelle"),
    ("1faab65c-c489-45ba-b302-b22d9e491d9f", "Jazz"),
    ("24e1a8ff-e5c7-464f-a708-c4fe92c59b28", "Johnny"),
    ("47906020-29e9-4903-91e3-8b66b0528410", "Kermit"),
    ("50570964-9672-4927-ac7d-40575e9112d3", "Kkona"),
    ("67dbd94d-a097-4676-af2f-1db67c1eb8dd", "Shadow"),
    ("1948c544-0eab-4408-9259-567b5b2059a4", "Trump"),
    ("68bc5eb0-fa1f-4d9b-b0bc-89f51edf5fb0", "Yuki"),
]


class PromptForm(forms.Form):
    message = forms.CharField(
        label="Message",
        max_length=1000,
        widget=forms.Textarea(attrs={"rows": 4, "cols": 50}),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        SOUND_BITE_DIRECTORY.mkdir(parents=True, exist_ok=True)
        self.sound_bites: list[Path] = sorted(
            (
                path
                for path in SOUND_BITE_DIRECTORY.iterdir()
                if path.is_file() and path.suffix.lower() in SOUND_BITE_EXTENSIONS
            ),
            key=lambda path: path.name.casefold(),
        )
        self.sound_bites_by_tag: dict[str, list[Path]] = {}
        for sound_bite in self.sound_bites:
            tag = sound_bite.stem.casefold()
            self.sound_bites_by_tag.setdefault(tag, []).append(sound_bite)

        self.voices_by_tag: dict[str, str] = {
            label.casefold(): voice_id for voice_id, label in VOICE_CHOICES
        }
        voice_tags = format_html_join(
            ", ",
            '<button type="button" class="tag-button" data-tag="{}">{}</button>',
            (
                (f"{{{label}}}", f"{{{label}}}")
                for _, label in VOICE_CHOICES
            ),
        )
        sound_tags = format_html_join(
            ", ",
            '<button type="button" class="tag-button" data-tag="{}">{}</button>',
            (
                (f"{{{path.stem}}}", f"{{{path.stem}}}")
                for path in self.sound_bites
            ),
        ) or "Aucune"
        self.fields["message"].help_text = format_html(
            "<strong>Balises voix :</strong> {}<br>"
            "<strong>Balises audio :</strong> {}",
            voice_tags,
            sound_tags,
        )

    def clean(self):
        cleaned_data = super().clean()
        message = cleaned_data.get("message", "")
        self.sound_bite_tags: dict[str, Path] = {}
        self.voice_tags: dict[str, str] = {}
        for tag in SOUND_BITE_TAG_PATTERN.findall(message):
            normalized_tag = tag.strip().casefold()
            matching_sounds = self.sound_bites_by_tag.get(normalized_tag, [])
            matching_voice = self.voices_by_tag.get(normalized_tag)
            if matching_sounds and matching_voice:
                self.add_error(
                    "message",
                    f"La balise {{{tag}}} correspond à la fois à un son et à une "
                    "voix. Renommez le fichier audio pour éviter le conflit.",
                )
            elif matching_voice:
                self.voice_tags[normalized_tag] = matching_voice
            elif not matching_sounds:
                self.add_error("message", f"Balise inconnue : {{{tag}}}.")
            elif len(matching_sounds) > 1:
                self.add_error(
                    "message",
                    f"Le nom {{{tag}}} correspond à plusieurs sons. "
                    "Renommez les fichiers pour rendre leurs noms uniques.",
                )
            else:
                self.sound_bite_tags[normalized_tag] = matching_sounds[0]

        if (
            self.voice_tags
            and not self.sound_bite_tags
            and not SOUND_BITE_TAG_PATTERN.sub("", message).strip()
        ):
            self.add_error("message", "Ajoutez du texte ou une balise de son.")

        return cleaned_data
