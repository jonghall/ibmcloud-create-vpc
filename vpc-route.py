#!/usr/bin/env python3
## VPC Route  - Add, View or Delete Routes.
## Author: Jon Hall
##

## Latest Next Gen API Spec: https://pages.github.ibm.com/riaas/api-spec/spec_genesis_2019-06-04/

import requests, json, time, sys, os, argparse, urllib

def parse_apiconfig(ini_file):
    ################################################
    ## Get APIKey from ini file
    ################################################

    dirpath = os.getcwd()
    config = configparser.ConfigParser()

    try:
        # attempt to open ini file to read apikey. Only proceed if found
        filepath = dirpath + "/" + ini_file
        open(filepath)

    except FileNotFoundError:
        raise Exception("Unable to find or open specified ini file.")
        quit()
    else:
        config.read(filepath)

    apikey = config["API"]["apikey"]

    return apikey

def getiamtoken(apikey):
    ################################################
    ## Get Bearer Token using apikey
    ################################################

    headers = {"Content-Type": "application/x-www-form-urlencoded",
               "Accept": "application/json"}

    parms = {"grant_type": "urn:ibm:params:oauth:grant-type:apikey", "apikey": apikey}

    try:
        resp = requests.post("https://iam.cloud.ibm.com/identity/token?" + urllib.parse.urlencode(parms),
                             headers=headers, timeout=30)
        resp.raise_for_status()
    except requests.exceptions.ConnectionError as errc:
        print("Error Connecting:", errc)
        quit()
    except requests.exceptions.Timeout as errt:
        print("Timeout Error:", errt)
        quit()
    except requests.exceptions.HTTPError as errb:
        print("Invalid token request.")
        print("template=%s" % parms)
        print("Error Data:  %s" % errb)
        print("Other Data:  %s" % resp.text)
        quit()

    iam = resp.json()

    iamtoken = {"Authorization": "Bearer " + iam["access_token"]}

    return iamtoken

def getvpcid(vpc_name):
    ################################################
    ## Lookup VPN ID by name
    ################################################

    # get list of VPCs in region to check if VPC already exists
    try:
        resp = requests.get(iaas_endpoint + '/v1/vpcs/' + version, headers=headers, timeout=30)
        resp.raise_for_status()
    except requests.exceptions.ConnectionError as errc:
        print("Error Connecting:", errc)
        quit()
    except requests.exceptions.Timeout as errt:
        print("Timeout Error:", errt)
        quit()
    except requests.exceptions.HTTPError as errb:
        unknownapierror(resp)

    if resp.status_code == 200:
        vpcs = json.loads(resp.content)["vpcs"]
        # Determine if network_acl name already exists and retreive id.
        vpc = list(filter(lambda vpc: vpc['name'] == vpc_name, vpcs))

        if len(vpc) > 0:
            vpcid = vpc[0]['id']
        else:
            vpcid = None
    else:
        vpcid = None

    return vpcid

def unknownapierror(resp):
    ################################################
    ## Handle Unknown RESPONSE CODE errors
    ################################################

    if resp.status_code >= 200 and resp.status_code < 300:
        print("Successful response, but unknown or unexpected response.")
        print("Response Code: %s" % (resp.status_code))
        print("Request Method: %s" % (resp.request.method))
        print("Request URL: %s" % (resp.request.url))
        print("Response: %s" % (resp.content))
        quit()

    if resp.status_code >= 300 and resp.status_code < 400:
        print("Your request was redirected resulting in an unknown or unexpected response.")
        print("Response Code: %s" % (resp.status_code))
        print("Request Method: %s" % (resp.request.method))
        print("Request URL: %s" % (resp.request.url))
        quit()

    if resp.status_code >= 400 and resp.status_code < 500:
        print("Unsuccessful response with an unexpected error code.")
        print("Response Code: %s" % (resp.status_code))
        print("Request Method: %s" % (resp.request.method))
        print("Request URL: %s" % (resp.request.url))
        print("Error Data: %s" % (json.loads(resp.content)['errors']))
        quit()

    return

def getregionendpoint(region):
    #############################
    # Get Region Availability
    #############################

    try:
        resp = requests.get(iaas_endpoint + '/v1/regions/' + region + version, headers=headers, timeout=30)
        resp.raise_for_status()
    except requests.exceptions.ConnectionError as errc:
        print("Error Connecting:", errc)
        quit()
    except requests.exceptions.Timeout as errt:
        print("Timeout Error:", errt)
        quit()
    except requests.exceptions.HTTPError as errb:
        unknownapierror(resp)

    if resp.status_code == 200:
        region = json.loads(resp.content)

        if region["status"] == "available":
            return region["endpoint"]
        else:
            print('Desired region is not currently available.')
            quit()
    else:
        unknownapierror(resp)
    return

def listroutes(vpc):
    # List the custom routes in a VPC
    vpcid = getvpcid(vpc)
    if vpcid is None:
        print("Invalid VPC")
        quit()
    else:
        # get list of VPCs in region to check if VPC already exists
        try:

            resp = requests.get(iaas_endpoint + '/v1/vpcs/' + vpcid + "/routes" + version, headers=headers, timeout=30)
            resp.raise_for_status()
        except requests.exceptions.ConnectionError as errc:
            print("Error Connecting:", errc)
            quit()
        except requests.exceptions.Timeout as errt:
            print("Timeout Error:", errt)
            quit()
        except requests.exceptions.HTTPError as errb:
            unknownapierror(resp)

        if resp.status_code == 200:
            routes = json.loads(resp.content)["routes"]
            for route in routes:
                print("Destination = %s, Next Hop = %s, Zone = %s" % (
                route["destination"], route["next_hop"]["address"], route["zone"]["name"]))
        return

def addroute(vpc, zone, destination, nexthop):
    # List the custom routes in a VPC
    vpcid = getvpcid(vpc)
    if vpcid is None:
        print("Invalid VPC")
        quit()
    else:
        # get list of VPCs in region to check if VPC already exists

        parms = {
            "destination": destination,
            "next_hop": {
                "address": nexthop,
            },
            "zone": {
                "name": zone,
            }
        }

        try:
            resp = requests.post(iaas_endpoint + '/v1/vpcs/' + vpcid + "/routes" + version, json=parms, headers=headers,
                                 timeout=30)
            resp.raise_for_status()
        except requests.exceptions.ConnectionError as errc:
            print("Error Connecting:", errc)
            quit()
        except requests.exceptions.Timeout as errt:
            print("Timeout Error:", errt)
            quit()
        except requests.exceptions.HTTPError as errb:
            unknownapierror(resp)

        if resp.status_code == 201:
            route = json.loads(resp.content)
            # print (json.dumps(route,indent=4))
            print("Route Add Succesfull")
            print("Destination = %s, Next Hop = %s, Zone = %s" % (
            route["destination"], route["next_hop"]["address"], route["zone"]["name"]))
        return


def deleteroute(vpc, routeid):
    reutrn
#####################################
# Set Global Variables
#####################################

iaas_endpoint = "https://us-south.iaas.cloud.ibm.com"
resource_controller_endpoint = "https://resource-controller.cloud.ibm.com"
version = "?version=2019-05-31"

#####################################
# Get Parameters
#####################################

parser = argparse.ArgumentParser(description="Create custom route in VPC .")
parser.add_argument("-k", "--apikey", default=os.environ.get('IC_API_KEY', None), help="IBM Cloud APIKey")
parser.add_argument("-v", "--vpc", help="Name of VPC to add route to.")
parser.add_argument("-a", "--action", default="list", help="VPC Route Action (list, add, delete)")
parser.add_argument("-g", "--generation", default=os.environ.get('IC_GENERATION'), help="Generation of VPC")
parser.add_argument("-r", "--region", default=os.environ.get('IC_REGION'), help="Region VPC resides.")
parser.add_argument("-z", "--zone", default="us-south-1", help="Zone for which route will be added.")
parser.add_argument("-d", "--destination", help="Destination Subnet for route.")
parser.add_argument("-n", "--nexthop", help="Next Hop for route to destination.")

args = parser.parse_args()

# Get the preferred VPC generation to use, default to gen 1 if not specified
headers = getiamtoken(args.apikey)

generation = args.generation

getregionendpoint(args.region)

if generation == "1":
    iaas_endpoint = getregionendpoint(args.region)
elif generation == "2":
    iaas_endpoint = "https://us-south.iaas.cloud.ibm.com"
else:
    print("Invalid Generation Flag.")
    quit()

version = version + '&generation=' + str(generation)

if args.action == "list":
    listroutes(args.vpc)
elif args.action == "add":
    addroute(args.vpc, args.zone, args.destination, args.nexthop)
elif args.action == "delete":
    print("not implemented.")
    quit()
else:
    print("Not a valid action.")
    quit()
