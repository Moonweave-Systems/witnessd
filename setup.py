from __future__ import annotations

from setuptools import find_packages, setup


setup(
    name="witnessd",
    version="2.38.0",
    description="witnessd execution engine with deprecated ORRO compatibility shim",
    packages=find_packages(include=["witnessd*", "orro*"]),
    data_files=[("share/witnessd", ["SKILL.md", "SKILL_INSPECT.md"])],
    python_requires=">=3.10",
    entry_points={
        "console_scripts": [
            "orro=orro.__main__:main",
        ],
    },
)
