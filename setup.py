from setuptools import setup, find_packages

setup(
    name="ridikc-content-harvester",
    version="2.0.0",
    description="YouTube downloader & media harvester (Python rewrite) by Ridikc.",
    packages=find_packages(),
    install_requires=[
        "requests>=2.31.0",
        "beautifulsoup4>=4.12.0",
        "yt-dlp>=2026.08.19",
        "flask>=3.0.0",
        "click>=8.1.0",
    ],
    entry_points={
        "console_scripts": [
            "rch=rch.cli:main",
        ],
    },
    python_requires=">=3.10",
)
