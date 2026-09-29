import os
import socket
import sys

DISABLED_ONLINE_FEATURES = (
    "COCOINDEX_DISABLE_USAGE_TRACKING", "HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE",
    "HF_DATASETS_OFFLINE", "HF_HUB_DISABLE_TELEMETRY", "DO_NOT_TRACK",
    "HF_HUB_DISABLE_PROGRESS_BARS",
)
NAME_RESOLUTION_EVENTS = {"socket.getaddrinfo", "socket.gethostbyname", "socket.gethostbyaddr"}
SOCKET_TRAFFIC_EVENTS = {"socket.connect", "socket.connect_ex", "socket.bind", "socket.sendto"}
INTERNET_ADDRESS_FAMILIES = {socket.AF_INET, socket.AF_INET6}


def enforce_offline_policy():
    for name in DISABLED_ONLINE_FEATURES:
        os.environ[name] = "1"
    os.environ["TOKENIZERS_PARALLELISM"] = "false"
    os.environ["COCOINDEX_MAX_INFLIGHT_COMPONENTS"] = "1"
    sys.addaudithook(_deny_internet_access)


def _deny_internet_access(event, args):
    if event in NAME_RESOLUTION_EVENTS:
        raise RuntimeError("Offline policy blocked a DNS lookup")
    if event in SOCKET_TRAFFIC_EVENTS and getattr(args[0], "family", None) in INTERNET_ADDRESS_FAMILIES:
        raise RuntimeError("Offline policy blocked an Internet socket operation")
