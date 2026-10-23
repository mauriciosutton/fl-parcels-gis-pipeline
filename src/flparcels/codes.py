"""Florida DOR county numbers and land use codes (Rule 12D-8.008, F.A.C.; NAL User's Guide)."""

# DOR numbers counties 11-77 alphabetically, with Miami-Dade filed under "Dade".
COUNTIES = {
    11: "Alachua", 12: "Baker", 13: "Bay", 14: "Bradford", 15: "Brevard", 16: "Broward",
    17: "Calhoun", 18: "Charlotte", 19: "Citrus", 20: "Clay", 21: "Collier", 22: "Columbia",
    23: "Miami-Dade", 24: "DeSoto", 25: "Dixie", 26: "Duval", 27: "Escambia", 28: "Flagler",
    29: "Franklin", 30: "Gadsden", 31: "Gilchrist", 32: "Glades", 33: "Gulf", 34: "Hamilton",
    35: "Hardee", 36: "Hendry", 37: "Hernando", 38: "Highlands", 39: "Hillsborough",
    40: "Holmes", 41: "Indian River", 42: "Jackson", 43: "Jefferson", 44: "Lafayette",
    45: "Lake", 46: "Lee", 47: "Leon", 48: "Levy", 49: "Liberty", 50: "Madison",
    51: "Manatee", 52: "Marion", 53: "Martin", 54: "Monroe", 55: "Nassau", 56: "Okaloosa",
    57: "Okeechobee", 58: "Orange", 59: "Osceola", 60: "Palm Beach", 61: "Pasco",
    62: "Pinellas", 63: "Polk", 64: "Putnam", 65: "St. Johns", 66: "St. Lucie",
    67: "Santa Rosa", 68: "Sarasota", 69: "Seminole", 70: "Sumter", 71: "Suwannee",
    72: "Taylor", 73: "Union", 74: "Volusia", 75: "Wakulla", 76: "Walton", 77: "Washington",
}

# Short labels for map popups; the rule text is longer.
LAND_USE = {
    "000": "Vacant Residential",
    "001": "Single Family",
    "002": "Mobile Home",
    "003": "Multi-Family (10+ Units)",
    "004": "Condominium",
    "005": "Cooperative",
    "006": "Retirement Home",
    "007": "Misc. Residential",
    "008": "Multi-Family (<10 Units)",
    "009": "Residential Common Area",
    "010": "Vacant Commercial",
    "011": "Store, One Story",
    "012": "Mixed Use Store/Office/Residential",
    "013": "Department Store",
    "014": "Supermarket",
    "015": "Regional Shopping Center",
    "016": "Community Shopping Center",
    "017": "Office, One Story",
    "018": "Office, Multi-Story",
    "019": "Professional Services",
    "020": "Airport, Terminal, Marina",
    "021": "Restaurant",
    "022": "Drive-In Restaurant",
    "023": "Financial Institution",
    "024": "Insurance Office",
    "025": "Repair Service Shop",
    "026": "Service Station",
    "027": "Auto Sales & Service",
    "028": "Parking Lot, Mobile Home Park",
    "029": "Wholesale Outlet",
    "030": "Florist, Greenhouse",
    "031": "Drive-In Theater, Open Stadium",
    "032": "Enclosed Theater, Auditorium",
    "033": "Nightclub, Bar",
    "034": "Bowling, Skating, Enclosed Arena",
    "035": "Tourist Attraction, Entertainment",
    "036": "Camp",
    "037": "Race Track",
    "038": "Golf Course, Driving Range",
    "039": "Hotel, Motel",
    "040": "Vacant Industrial",
    "041": "Light Manufacturing",
    "042": "Heavy Industrial",
    "043": "Lumber Yard, Sawmill",
    "044": "Packing Plant",
    "045": "Cannery, Brewery, Distillery",
    "046": "Food Processing",
    "047": "Mineral Processing",
    "048": "Warehousing, Distribution",
    "049": "Open Storage, Junk Yard",
    "050": "Improved Agricultural",
    "051": "Cropland Class I",
    "052": "Cropland Class II",
    "053": "Cropland Class III",
    "054": "Timberland (Site Index 90+)",
    "055": "Timberland (Site Index 80-89)",
    "056": "Timberland (Site Index 70-79)",
    "057": "Timberland (Site Index 60-69)",
    "058": "Timberland (Site Index 50-59)",
    "059": "Timberland (Not Classified)",
    "060": "Grazing Land Class I",
    "061": "Grazing Land Class II",
    "062": "Grazing Land Class III",
    "063": "Grazing Land Class IV",
    "064": "Grazing Land Class V",
    "065": "Grazing Land Class VI",
    "066": "Orchard, Grove",
    "067": "Poultry, Bees, Fish, Rabbits",
    "068": "Dairy, Feed Lot",
    "069": "Ornamentals, Misc. Agricultural",
    "070": "Vacant Institutional",
    "071": "Church",
    "072": "Private School, College",
    "073": "Private Hospital",
    "074": "Home for the Aged",
    "075": "Non-Profit, Charitable Service",
    "076": "Mortuary, Cemetery",
    "077": "Club, Lodge, Union Hall",
    "078": "Sanitarium, Rest Home",
    "079": "Cultural Facility",
    "080": "Vacant Governmental",
    "081": "Military",
    "082": "Forest, Park, Recreation Area",
    "083": "Public School",
    "084": "Public College",
    "085": "Public Hospital",
    "086": "County Government",
    "087": "State Government",
    "088": "Federal Government",
    "089": "Municipal Government",
    "090": "Government Leasehold",
    "091": "Utility",
    "092": "Mining, Petroleum, Gas Land",
    "093": "Subsurface Rights",
    "094": "Right-of-Way",
    "095": "Rivers, Lakes, Submerged Land",
    "096": "Waste Land, Marsh, Swamp",
    "097": "Outdoor Recreation, Parkland",
    "098": "Centrally Assessed",
    "099": "Non-Agricultural Acreage",
}


def land_use_group(code: str) -> str:
    """DOR's top-level category for a three-digit land use code."""
    n = int(code)
    if n <= 9:
        return "Residential"
    if n <= 39:
        return "Commercial"
    if n <= 49:
        return "Industrial"
    if n <= 69:
        return "Agricultural"
    if n <= 79:
        return "Institutional"
    if n <= 89:
        return "Governmental"
    if n <= 97:
        return "Miscellaneous"
    if n == 98:
        return "Centrally Assessed"
    return "Non-Agricultural Acreage"
