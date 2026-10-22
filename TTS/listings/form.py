from django import forms

from TTS.chatbot import VOICE_ID


class PromptForm(forms.Form):
    message = forms.CharField(
        label="Message",
        max_length=1000,
        widget=forms.Textarea(attrs={"rows": 4, "cols": 50}),
    )
    voice_id = forms.ChoiceField(
        label="Voix",
        choices=[
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


        ],
        initial=VOICE_ID,
    )