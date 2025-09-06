# === RESTAURANTS (pickup) - University of Illinois Chicago (UIC) =====
RESTAURANTS = {
    "Chipotle": {
        "address": "1132 S Clinton St, Chicago, IL 60607",
        "lat": 41.8678,
        "lng": -87.6410,
    },
    "McDonald's": {
        "address": "2315 W Ogden Ave, Chicago, IL 60608",
        "lat": 41.8630,
        "lng": -87.6861,
    },
    "Chick-fil-A": {
        "address": "1106 S Clinton St, Chicago, IL 60607",
        "lat": 41.8679,
        "lng": -87.6410,
    },
    "Portillo's": {
        "address": "520 W Taylor St, Chicago, IL 60607",
        "lat": 41.8697,
        "lng": -87.6407,
    },
    "Starbucks": {
        "address": "1430 W Taylor St, Chicago, IL 60607",
        "lat": 41.8692,
        "lng": -87.6629,
    },
}

# convenience list for FAQ answers or drop-downs
AVAILABLE_RESTAURANTS = list(RESTAURANTS.keys())

# Drop-off locations are now handled dynamically via Google API
# No more hardcoded drop-off locations