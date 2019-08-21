#!/usr/bin/env python3
## VPC Route  - Add, View or Delete Routes.
## Author: Jon Hall
##

## Latest Next Gen API Spec: https://pages.github.ibm.com/riaas/api-spec/spec_genesis_2019-06-04/

import requests, json, time, sys, yaml, os, argparse, configparser, urllib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText


def main(region, generation, topology):
    if "resource_group" in topology:
        resource_group = topology["resource_group"]
    else:
        resource_group = "default"

    resource_group_id = getresourcegroupid(resource_group)

    # Create Network acls
    if 'network_acls' in topology:
        for network_acl in topology["network_acls"]:
            createnetworkacl(network_acl)

    # If Gen1 get default_network ACL, if not skip
    if generation == 1:
        if "default_network_acl" in topology:
            default_network_acl = topology["default_network_acl"]
        else:
            default_network_acl = vpc_name + "-default-acl"
        if "classic_access" in topology and generation == 1:
            classic_access = topology["classic_access"]
        else:
            classic_access = False
    else:
        default_network_acl = None
        classic_access = None

    # Create the VPC
    vpc_name = topology["vpc"]
    vpcid = createvpc(vpc_name, region, classic_access, resource_group_id, default_network_acl)

    # Create VPC's security groups
    for security_group in topology['security_groups']:
        createsecuritygroup(security_group, vpcid)

    # Create sshKeys for VPC
    for sshkey in topology["sshkeys"]:
        createsshkey(sshkey)

    ###############################################################################
    # Iterate through zones creating address-prefixes, subnets, vpns, and instances
    ###############################################################################

    for zone in topology["zones"]:
        # Create vpc-address-prefix for zone
        if "address_prefix_cidr" in zone:
            createaddressprefix(vpcid, zone['name'], zone['address_prefix_cidr'])
        # Create Subnets
        for subnet in zone["subnets"]:
            ## Provision new Subnet
            subnet_id = createsubnet(vpcid, zone["name"], subnet)

            # Check if Public Gateway is required by Subnet
            if 'publicGateway' in subnet:
                if subnet["publicGateway"]:
                    # A gateway is needed check if Public Gateway already exists in zone, if not create
                    publicgatewayid = getpublicgatewayid(zone["name"], vpcid)

                    if publicgatewayid is None:
                        # Does not exists, so create public gateway in zone
                        gateway_name = vpc_name + "-" + zone["name"] + "-gw"
                        publicgatewayid = createpublicgateway(gateway_name, zone["name"], vpcid)

                    # either attach existing public gateway, or the one just one just requested
                    attachpublicgateway(publicgatewayid, subnet_id)

            if "vpn" in subnet:
                for vpn_instance in subnet["vpn"]:
                    # A VPN instance is needed
                    # local_CIDR derviced from the zone address block
                    createvpn(vpn_instance, zone["address_prefix_cidr"], subnet_id, resource_group_id)

            # Build instances for this subnet (if defined in topology)
            if "instances" in subnet:
                for instance in subnet["instances"]:
                    template = getinstancetemplate(topology["instanceTemplates"], instance["template"])
                    profile_name = template["profile_name"]
                    sshkey_name = template["sshkey"]
                    security_group = instance["security_group"]

                    if "bandwidth" in template and generation == 2:
                        bandwidth = template["bandwidth"]
                    else:
                        bandwidth = None

                    user_data = encodecloudinit(template["cloud-init-file"])

                    image_id = getimageid(template["image"])
                    if image_id == 0:
                        print("Can't create instances.  The Image named %s does not exist." % image_name)
                        quit()

                    sshkey_id = getsshkeyid(template["sshkey"])
                    if image_id == 0:
                        print("Can't create instances.  The ssh key named %s does not exist." % sshkey_name)
                        quit()

                    for q in range(1, instance["quantity"] + 1):
                        instance_name = (instance["name"] % q) + "-" + zone["name"]
                        volumes = []
                        if "volumes" in template:
                            for r in range(len(template["volumes"])):
                                volume = {
                                    "volume": {
                                        "name": instance_name + '-secondary-vol-' + str(r),
                                        "capacity": template["volumes"][r]["capacity"],
                                    },
                                    "delete_volume_on_instance_delete": template["volumes"][r][
                                        "delete_volume_on_instance_delete"]
                                }

                                if "profile" in template["volumes"][r]:
                                    volume["volume"]["profile"] = {"name": template["volumes"][r]["profile"]}
                                else:
                                    volume["iops"] = template["volumes"][r]["iops"]

                                volumes.append(volume)

                        instance_id = createinstance(zone["name"], instance_name, vpcid, image_id, profile_name,
                                                     sshkey_id,
                                                     subnet_id,
                                                     security_group,
                                                     user_data,
                                                     volumes)
                        # IF floating_ip = True assign
                        if 'floating_ip' in instance:
                            if instance['floating_ip']:
                                floating_ip_id, floating_ip_address = assignfloatingip(instance_id)

    #######################################################################
    # Create load balancers specified
    #######################################################################

    if "load_balancers" in topology:
        for lb in topology["load_balancers"]:
            lb_id = createloadbalancer(lb, resource_group_id)

    return


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


def createsubnet(vpcid, zone_name, subnet):
    ################################################
    ## Create new subnet is zone
    ################################################

    # get list of subnets in region to check if subnet already exists
    subnet_name = subnet["name"]
    subnetid = getsubnetid(subnet["name"])

    if subnetid is None:
        # Subnet does not exist so create it

        if "network_acl" in subnet:
            network_acl_id = getnetworkaclid(subnet['network_acl'])
        else:
            network_acl_id = None

        if generation == 1 and network_acl_id is None:
            # Error stop processing, ACL does not exist to assign to subnet
            print("Network ACL named %s does not exists" % (subnet['network_acl']))
            quit()

        parms = {"name": subnet_name,
                 "ipv4_cidr_block": subnet['ipv4_cidr_block'],
                 "zone": {"name": zone_name},
                 "vpc": {"id": vpcid}
                 }

        # If Gen1 add network acl id to parameters
        if generation == 1:
            parms["network_acl"] = {"id": network_acl_id}

        try:
            resp = requests.post(iaas_endpoint + '/v1/subnets' + version, json=parms, headers=headers, timeout=30)
            resp.raise_for_status()
        except requests.exceptions.ConnectionError as errc:
            print("Error Connecting:", errc)
            quit()
        except requests.exceptions.Timeout as errt:
            print("Timeout Error:", errt)
            quit()
        except requests.exceptions.HTTPError as errb:
            if resp.status_code == 400:
                print("Invalid subnet template provided.")
                print("template: %s" % parms)
                print("Error Data:  %s" % errb)
                quit()
            elif resp.status_code == 409:
                print("The subnet template conflicts with another subnet in this VPC.")
                print("template: %s" % parms)
                print("Error Data:  %s" % errb)
                quit()
            else:
                unknownapierror(resp)

        if resp.status_code == 201:
            print("Subnet %s requested in zone %s." % (subnet_name, zone_name))
            newsubnet = resp.json()
            count = 0
            while count < 12:
                resp = requests.get(iaas_endpoint + '/v1/subnets/' + newsubnet["id"] + version, headers=headers);
                subnet_status = json.loads(resp.content)["status"]
                if subnet_status == "available":
                    break
                else:
                    print(
                        "Waiting for subnet creation to complete before proceeding.   Sleeping for 5 seconds...")
                    count += 1
                    time.sleep(5)
            print("Subnet %s named %s was created successfully in zone %s." % (
                newsubnet["id"], subnet_name, zone_name))
            subnetid = newsubnet["id"]
        else:
            unknownapierror(resp)
    else:
        # Subnet already exists do not create
        print("Subnet %s (%s) already exists in zone. " % (subnet_name, subnetid))

    return subnetid


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
