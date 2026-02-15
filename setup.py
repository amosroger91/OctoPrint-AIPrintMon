from setuptools import setup

setup(
    name="octoprint_ai_printmon",
    version="0.0.1",
    description="AI-based print monitoring plugin for OctoPrint (skeleton)",
    packages=["octoprint_ai_printmon"],
    install_requires=["requests"],
    entry_points={
        "octoprint.plugin": [
            "ai_printmon = octoprint_ai_printmon:AIPrintMonPlugin"
        ]
    }
)
