from __future__ import annotations

from setuptools import find_packages, setup


setup(
    name="witnessd",
    version="2.2.0",
    description="witnessd execution engine and witnessd-hosted ORRO product surface",
    packages=find_packages(include=["witnessd*", "orro*"]),
    python_requires=">=3.10",
    entry_points={
        "console_scripts": [
            "orro=orro.__main__:main",
        ],
    },
)
