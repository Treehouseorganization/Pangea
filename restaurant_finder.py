#!/usr/bin/env python3
"""
Restaurant Finder using Google Maps Places API
Finds the closest restaurants of specific chains near university locations
"""

import requests
import os
from typing import Dict, List, Tuple, Optional

class RestaurantFinder:
    def __init__(self, api_key: str):
        self.api_key = api_key
        self.base_url = "https://maps.googleapis.com/maps/api/place"
        
        # Restaurant chains to search for
        self.target_chains = [
            "Chipotle",
            "McDonald's", 
            "Chick-fil-A",
            "Portillo's",
            "Starbucks"
        ]
        
        # University locations
        self.universities = {
            "DePaul University": {
                "address": "1 E Jackson Blvd, Chicago, IL 60604",
                "lat": 41.8781,
                "lng": -87.6298
            },
            "Northern Illinois University": {
                "address": "1425 W Lincoln Hwy, DeKalb, IL 60115",
                "lat": 41.9312,
                "lng": -88.7537
            },
            "Western Illinois University": {
                "address": "1 University Cir, Macomb, IL 61455",
                "lat": 40.4648,
                "lng": -90.6712
            }
        }

    def geocode_address(self, address: str) -> Optional[Tuple[float, float]]:
        """Convert address to lat/lng coordinates"""
        geocode_url = f"{self.base_url}/geocode/json"
        params = {
            'address': address,
            'key': self.api_key
        }
        
        try:
            response = requests.get(geocode_url, params=params)
            response.raise_for_status()
            data = response.json()
            
            if data['status'] == 'OK' and data['results']:
                location = data['results'][0]['geometry']['location']
                return (location['lat'], location['lng'])
            else:
                print(f"Geocoding failed: {data.get('status', 'Unknown error')}")
                return None
        except Exception as e:
            print(f"Geocoding error: {e}")
            return None

    def find_nearby_restaurants(self, lat: float, lng: float, radius: int = 3000) -> List[Dict]:
        """Find nearby restaurants using Places API"""
        nearby_url = f"{self.base_url}/nearbysearch/json"
        params = {
            'location': f"{lat},{lng}",
            'radius': radius,
            'type': 'restaurant',
            'key': self.api_key
        }
        
        try:
            response = requests.get(nearby_url, params=params)
            response.raise_for_status()
            data = response.json()
            
            if data['status'] == 'OK':
                return data['results']
            else:
                print(f"Places search failed: {data.get('status', 'Unknown error')}")
                return []
        except Exception as e:
            print(f"Places API error: {e}")
            return []

    def search_specific_chain(self, lat: float, lng: float, chain_name: str, radius: int = 8000) -> List[Dict]:
        """Search for a specific restaurant chain"""
        nearby_url = f"{self.base_url}/nearbysearch/json"
        params = {
            'location': f"{lat},{lng}",
            'radius': radius,
            'keyword': chain_name,
            'type': 'restaurant',
            'key': self.api_key
        }
        
        try:
            response = requests.get(nearby_url, params=params)
            response.raise_for_status()
            data = response.json()
            
            if data['status'] == 'OK':
                # Filter results to only include places that contain the chain name
                filtered_results = []
                for place in data['results']:
                    place_name = place.get('name', '').lower()
                    if chain_name.lower() in place_name:
                        filtered_results.append(place)
                return filtered_results
            else:
                print(f"Chain search failed for {chain_name}: {data.get('status', 'Unknown error')}")
                return []
        except Exception as e:
            print(f"Chain search error for {chain_name}: {e}")
            return []

    def calculate_distance(self, lat1: float, lng1: float, lat2: float, lng2: float) -> float:
        """Calculate distance between two points using Haversine formula (approximate)"""
        import math
        
        # Convert to radians
        lat1_r, lng1_r = math.radians(lat1), math.radians(lng1)
        lat2_r, lng2_r = math.radians(lat2), math.radians(lng2)
        
        # Haversine formula
        dlat = lat2_r - lat1_r
        dlng = lng2_r - lng1_r
        a = math.sin(dlat/2)**2 + math.cos(lat1_r) * math.cos(lat2_r) * math.sin(dlng/2)**2
        c = 2 * math.asin(math.sqrt(a))
        
        # Earth radius in miles
        r = 3956
        return c * r

    def find_closest_chain_restaurants(self, university_name: str) -> Dict:
        """Find the closest restaurant of each target chain near a university"""
        if university_name not in self.universities:
            print(f"University {university_name} not found")
            return {}
            
        uni_data = self.universities[university_name]
        uni_lat, uni_lng = uni_data['lat'], uni_data['lng']
        
        print(f"\n🎓 Finding restaurants near {university_name}")
        print(f"📍 Location: {uni_data['address']}")
        print(f"📌 Coordinates: {uni_lat}, {uni_lng}")
        print("=" * 60)
        
        results = {}
        
        for chain in self.target_chains:
            print(f"\n🔍 Searching for {chain}...")
            chain_results = self.search_specific_chain(uni_lat, uni_lng, chain)
            
            if not chain_results:
                print(f"   ❌ No {chain} locations found")
                continue
                
            # Find the closest location
            closest_location = None
            closest_distance = float('inf')
            
            for place in chain_results:
                place_lat = place['geometry']['location']['lat']
                place_lng = place['geometry']['location']['lng']
                distance = self.calculate_distance(uni_lat, uni_lng, place_lat, place_lng)
                
                if distance < closest_distance:
                    closest_distance = distance
                    closest_location = place
            
            if closest_location:
                results[chain] = {
                    'name': closest_location['name'],
                    'address': closest_location.get('vicinity', 'Address not available'),
                    'lat': closest_location['geometry']['location']['lat'],
                    'lng': closest_location['geometry']['location']['lng'],
                    'distance_miles': round(closest_distance, 2),
                    'rating': closest_location.get('rating', 'No rating'),
                    'price_level': closest_location.get('price_level', 'Unknown'),
                    'place_id': closest_location['place_id']
                }
                print(f"   ✅ Found: {closest_location['name']} ({closest_distance:.2f} miles)")
            else:
                print(f"   ❌ No valid {chain} locations found")
        
        return results

    def format_results_as_python_dict(self, university_name: str, results: Dict) -> str:
        """Format results as Python dictionary similar to current hardcoded format"""
        formatted = f"# Restaurants near {university_name}\n"
        formatted += "RESTAURANTS = {\n"
        
        for chain, data in results.items():
            formatted += f'    "{chain}": {{\n'
            formatted += f'        "address": "{data["address"]}",\n'
            formatted += f'        "lat": {data["lat"]},\n'
            formatted += f'        "lng": {data["lng"]},\n'
            formatted += f'        "distance_miles": {data["distance_miles"]},\n'
            formatted += f'        "rating": {data["rating"]},\n'
            formatted += f'        "place_id": "{data["place_id"]}",\n'
            formatted += f'    }},\n'
        
        formatted += "}\n"
        return formatted

    def run_for_university(self, university_name: str):
        """Run the restaurant finder for a specific university"""
        results = self.find_closest_chain_restaurants(university_name)
        
        if results:
            print(f"\n📋 RESULTS FOR {university_name.upper()}:")
            print("=" * 60)
            for chain, data in results.items():
                print(f"{chain}:")
                print(f"  📍 Address: {data['address']}")
                print(f"  📏 Distance: {data['distance_miles']} miles")
                print(f"  ⭐ Rating: {data['rating']}")
                print(f"  💰 Price Level: {data['price_level']}")
                print(f"  🆔 Place ID: {data['place_id']}")
                print()
            
            # Generate formatted Python code
            python_dict = self.format_results_as_python_dict(university_name, results)
            print("\n🐍 PYTHON DICTIONARY FORMAT:")
            print("=" * 60)
            print(python_dict)
        else:
            print(f"❌ No restaurants found for {university_name}")

    def run_for_all_universities(self):
        """Run the restaurant finder for all universities"""
        for university_name in self.universities.keys():
            self.run_for_university(university_name)
            print("\n" + "="*80 + "\n")


def main():
    # Get API key from environment variable
    api_key = os.getenv('GOOGLE_MAPS_API_KEY')
    
    if not api_key:
        print("❌ Error: Please set GOOGLE_MAPS_API_KEY environment variable")
        print("💡 You can get an API key from: https://developers.google.com/maps/documentation/places/web-service/get-api-key")
        print("💡 Then run: export GOOGLE_MAPS_API_KEY='your_api_key_here'")
        return
    
    finder = RestaurantFinder(api_key)
    
    # Run for all three universities as requested
    finder.run_for_all_universities()


if __name__ == "__main__":
    main()