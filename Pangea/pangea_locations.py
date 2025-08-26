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

# === LOCATION NORMALIZATION ==========================================
LOCATION_ALIASES = {
    # Richard J Daley Library variations
    "library": "Richard J Daley Library",
    "daley library": "Richard J Daley Library", 
    "main library": "Richard J Daley Library",
    "the library": "Richard J Daley Library",
    "richard daley library": "Richard J Daley Library",
    "richard j daley": "Richard J Daley Library",
    "daley": "Richard J Daley Library",
    
    # Student Center East variations
    "student center east": "Student Center East",
    "sce": "Student Center East",
    "east student center": "Student Center East",
    "student center e": "Student Center East",
    
    # Student Center West variations  
    "student center west": "Student Center West",
    "scw": "Student Center West",
    "west student center": "Student Center West",
    "student center w": "Student Center West",
    
    # Student Services Building variations
    "student services": "Student Services Building",
    "ssb": "Student Services Building",
    "student services building": "Student Services Building",
    "services building": "Student Services Building",
    
    # University Hall variations
    "university hall": "University Hall",
    "uh": "University Hall",
    "u hall": "University Hall",
    "uni hall": "University Hall",
}

def normalize_location(location_input):
    """
    Normalize location input to canonical location name.
    
    Args:
        location_input (str): Raw location input from user
        
    Returns:
        str: Canonical location name if found, original input if not found
    """
    if not location_input:
        return location_input
        
    # Convert to lowercase and strip whitespace for matching
    normalized_input = location_input.lower().strip()
    
    # Check if it's already a canonical name (case insensitive)
    for canonical_name in DROPOFFS.keys():
        if normalized_input == canonical_name.lower():
            return canonical_name
    
    # Check aliases
    if normalized_input in LOCATION_ALIASES:
        return LOCATION_ALIASES[normalized_input]
    
    # Return original input if no match found
    return location_input