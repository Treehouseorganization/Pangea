# === RESTAURANTS (pickup) =============================================
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

# === DROP-OFF LOCATIONS ===============================================
DROPOFFS = {
    "Richard J Daley Library": {
        "address": "801 S Morgan St, Chicago, IL 60607",
        "lat": 41.8718,
        "lng": -87.6498,
        "phone": "+1 312-996-2724",
    },
    "Student Center East": {
        "address": "750 S Halsted St, Chicago, IL 60607",
        "lat": 41.8719,
        "lng": -87.6480,
    },
    "Student Center West": {
        "address": "828 S Wolcott Ave, Chicago, IL 60612",
        "lat": 41.8702,
        "lng": -87.6704,
    },
    "Student Services Building": {
        "address": "1200 W Harrison St, Chicago, IL 60607",
        "lat": 41.8746,
        "lng": -87.6584,
    },
    "University Hall": {
        "address": "601 S Morgan St, Chicago, IL 60607",
        "lat": 41.8742,
        "lng": -87.6518,
    },
}

AVAILABLE_DROPOFF_LOCATIONS = list(DROPOFFS.keys())