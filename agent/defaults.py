"""기본 설정값. 사용자가 앱 화면에서 바꾼 값은 settings.json 에 저장되어 이 위에 덮입니다."""

DEFAULTS = {
    "capcut_drafts": "",      # 비우면 자동 탐색
    "seed_draft": "",         # 자막 스타일 견본으로 쓸 초안 이름 (앱에서 선택)

    "transcribe": {
        "model": "large-v3-turbo",
        "language": "ko",
        "device": "cpu",
        "compute_type": "int8",
        "initial_prompt": "",
    },

    "cut": {
        "silence": {
            "enabled": True,
            "min_duration": 0.12,  # 이보다 짧은 비발화 틈은 굳이 안 자름
        },
        "stutter": {
            "enabled": True,
            "similarity": 0.85,
            "max_gap": 1.2,
        },
        "retake": {
            "enabled": True,
            "similarity": 0.72,
            "lookahead": 3,
            "max_gap": 25.0,
            "keep": "last",
        },
        "lead_in": 0.08,
        "lead_out": 0.14,
        "min_clip": 0.30,
        "merge_gap": 0.18,
    },

    "subtitle": {
        "enabled": True,
        "min_duration": 0.7,
        "max_duration": 4.0,
        "strip_punctuation": True,
        "position_y": 0.78,
        "font_size": 8.0,
    },

    "draft": {
        "name_pattern": "{name}_auto_{time}",
        "width": 1080,
        "height": 1920,
        "fps": 30,
        "fit": "auto",
    },
}

# 앱 화면의 '간단 설정' 3개가 실제로 건드리는 값들.
# 사용자는 세기만 고르고, 숫자는 여기서 정합니다.
PRESETS = {
    "cut_strength": {
        "loose": {  # 여유롭게 — 말맛을 살림
            "cut.silence.min_duration": 0.35,
            "cut.lead_in": 0.12,
            "cut.lead_out": 0.20,
            "cut.retake.similarity": 0.85,
        },
        "normal": {
            "cut.silence.min_duration": 0.15,
            "cut.lead_in": 0.08,
            "cut.lead_out": 0.14,
            "cut.retake.similarity": 0.72,
        },
        "tight": {  # 촘촘하게 — 말 사이 틈을 거의 다 잘라냄
            "cut.silence.min_duration": 0.06,
            "cut.lead_in": 0.04,
            "cut.lead_out": 0.08,
            "cut.retake.similarity": 0.65,
        },
    },
    "quality": {
        "fast": {"transcribe.model": "small"},
        "balanced": {"transcribe.model": "large-v3-turbo"},
        "best": {"transcribe.model": "large-v3"},
    },
    "ratio": {
        "vertical": {"draft.width": 1080, "draft.height": 1920},
        "square": {"draft.width": 1080, "draft.height": 1080},
        "wide": {"draft.width": 1920, "draft.height": 1080},
        "source": {"draft.width": 0, "draft.height": 0},  # 원본 그대로
    },
}
