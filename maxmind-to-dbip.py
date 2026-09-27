#!/usr/bin/env python3
"""Convert a GeoLite2 Country CSV ZIP to xt_geoip_build's DB-IP CSV format."""

import csv
import io
import ipaddress
from pathlib import PurePosixPath
import re
import sys
import zipfile


def rows(archive, filename, required):
    matches = [name for name in archive.namelist()
               if PurePosixPath(name).name == filename]
    if len(matches) != 1:
        raise ValueError(f"Expected exactly one {filename}")
    # Read members directly: no archive paths are extracted onto the filesystem.
    with archive.open(matches[0]) as raw:
        with io.TextIOWrapper(raw, encoding="utf-8-sig", newline="") as source:
            reader = csv.DictReader(source, strict=True)
            if not required.issubset(reader.fieldnames or []):
                raise ValueError(f"Missing required columns in {filename}")
            for row in reader:
                if None in row or any(value is None for value in row.values()):
                    raise ValueError(f"Malformed row in {filename}")
                yield row


def convert(source, destination):
    prefix = "GeoLite2-Country"
    with zipfile.ZipFile(source) as archive:
        countries = {}
        for row in rows(archive, f"{prefix}-Locations-en.csv",
                        {"geoname_id", "country_iso_code"}):
            code = row["country_iso_code"]
            if code and not re.fullmatch(r"[A-Z]{2}", code):
                raise ValueError("Invalid country ISO code")
            countries[row["geoname_id"]] = code
        with open(destination, "w", encoding="utf-8", newline="") as output:
            writer = csv.writer(output, lineterminator="\n")
            for version in (4, 6):
                count = 0
                previous_end = -1
                for row in rows(archive, f"{prefix}-Blocks-IPv{version}.csv",
                                {"network", "geoname_id"}):
                    network = ipaddress.ip_network(row["network"])
                    if network.version != version:
                        raise ValueError("Wrong IP family in blocks file")
                    if int(network.network_address) <= previous_end:
                        raise ValueError("Unsorted or overlapping networks")
                    previous_end = int(network.broadcast_address)
                    location = row["geoname_id"]
                    if location and location not in countries:
                        raise ValueError("Unknown geoname_id in blocks file")
                    # Use geographic country only, never ISP registration or
                    # represented country as a fallback for unknown locations.
                    code = countries.get(location)
                    if not code:
                        continue
                    writer.writerow((network.network_address,
                                     network.broadcast_address, code))
                    count += 1
                if not count:
                    raise ValueError(f"No geolocated IPv{version} ranges")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit("Usage: maxmind-to-dbip.py SOURCE.zip DESTINATION.csv")
    try:
        convert(sys.argv[1], sys.argv[2])
    except (OSError, ValueError, KeyError, csv.Error, zipfile.BadZipFile) as error:
        sys.exit(f"MaxMind conversion failed: {error}")
