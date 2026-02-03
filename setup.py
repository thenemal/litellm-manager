from setuptools import setup, find_packages

setup(
    name="litellm-manager",
    version="0.1.0",
    packages=find_packages(),
    python_requires=">=3.10",
    install_requires=["pyyaml"],
    entry_points={
        "console_scripts": [
            "ltm=ltm.cli:main",
        ],
    },
)
