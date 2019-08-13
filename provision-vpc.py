#!/usr/bin/env python3
## Provision-vpc - A script to provision vpc, network, and compute resources based on a templated topology yaml file.
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

def getzones(region):
    #############################
    # Get list of zones in Region
    #############################

    try:
        resp = requests.get(iaas_endpoint + '/v1/regions/' + region + '/zones' + version, headers=headers, timeout=30)
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
        zones = json.loads(resp.content)["zones"]
        if len(zones) > 0:
            print("There are %s zones in the %s region" % (len(zones), region))
        else:
            print("There are no zones available in this region.")
            quit()
    else:
        unknownapierror(resp)
    return zones

def getregionavailability(region):
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

        if topology["region"] == region["name"] and region["status"] == "available":
            print("Region %s region is available." % region["name"])
            return region
        else:
            print('Desired region is not currently available.')
            quit()
    else:
        unknownapierror(resp)
    return

def getnetworkaclid(network_acl_name):
    ################################################
    ## Lookup network acl id by name
    ################################################

    try:
        resp = requests.get(iaas_endpoint + '/v1/network_acls/' + version, headers=headers, timeout=30)
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
        acls = json.loads(resp.content)["network_acls"]
        default_network_acl = \
            list(filter(lambda acl: acl['name'] == network_acl_name, acls))

        if len(default_network_acl) > 0:
            networkaclid = default_network_acl[0]['id']

        else:
            networkaclid = None
    else:
        unknownapierror(resp)

    return networkaclid

def createnetworkacl(network_acl):
    ################################################
    ## create network acl
    ################################################

    # check if ACL already exists by checking for id
    networkaclid = getnetworkaclid(network_acl["network_acl"])
    if networkaclid is None:
        # Network ACL does not exist create it

        rules = []
        for rule in network_acl['rules']:
            new_rule = {}

            if "action" in rule:
                new_rule["action"] = rule["action"]

            if "direction" in rule:
                new_rule["direction"] = rule["direction"]

            if "name" in rule:
                new_rule["name"] = network_acl["network_acl"] + "-" + rule["name"]

            if "source" in rule:
                new_rule["source"] = rule["source"]

            if "destination" in rule:
                new_rule["destination"] = rule["destination"]

            if "protocol" in rule:
                new_rule["protocol"] = rule["protocol"]
                if rule["protocol"] == "tcp" or rule["protocol"] == "udp":

                    if "port_min" in rule:
                        new_rule["port_min"] = rule["port_min"]
                    if "port_max" in rule:
                        new_rule["port_max"] = rule["port_max"]
                    if "source_port_min" in rule:
                        new_rule["source_port_min"] = rule["source_port_min"]
                    if "source_port_max" in rule:
                        new_rule["source_port_max"] = rule["source_port_max"]

                if rule["protocol"] == "icmp":
                    if "type" in rule:
                        new_rule["type"] = rule["type"]
                    if "code" in rule:
                        new_rule["code"] = rule["code"]
            else:
                new_rule["protocol"] = "all"


            rules.append(new_rule)

        parms = {
            "name": network_acl["network_acl"],
            "rules": rules,
        }

        try:
            resp = requests.post(iaas_endpoint + '/v1/network_acls' + version, json=parms, headers=headers, timeout=30)
            resp.raise_for_status()
        except requests.exceptions.ConnectionError as errc:
            print("Error Connecting:", errc)
            quit()
        except requests.exceptions.Timeout as errt:
            print("Timeout Error:", errt)
            quit()
        except requests.exceptions.HTTPError as errb:
            if resp.status_code == 400:
                print("Invalid network_acl template provided.")
                print("template=%s" % parms)
                print("Error Data:  %s" % errb)
                print("Other Data:  %s" % resp.text)
                quit()
            else:

                unknownapierror(resp)

        if resp.status_code == 201:
            network_acl = resp.json()
            print("Network ACL %s (%s) was created successfully." % (network_acl["name"], network_acl["id"]))
        else:
            unknownapierror(resp)
    else:
        # Network ACL already exists.  do no recreate
        print("Network ACL %s already exists." % (network_acl["network_acl"]))

    return networkaclid


def getsecuritygroupid(security_group, vpcid):
    ################################################
    ## Lookup security group id by name
    ################################################

    try:
        resp = requests.get(iaas_endpoint + '/v1/security_groups/' + version + "&vpc.if=" + vpcid, headers=headers,
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

    if resp.status_code == 200:
        sgs = json.loads(resp.content)["security_groups"]
        default_security_group = \
            list(filter(lambda acl: acl['name'] == security_group, sgs))

        if len(default_security_group) > 0:
            security_group_id = default_security_group[0]['id']
        else:
            security_group_id = None
    else:
        unknownapierror(resp)

    return security_group_id


def createsecuritygroup(security_group, vpcid):
    ################################################
    ## create security group
    ################################################

    # check if security group already exists by checking for id
    securitygroupid = getsecuritygroupid(security_group["security_group"], vpcid)
    if securitygroupid is None:
        # security group does not exist create it

        rules = []
        for rule in security_group['rules']:
            new_rule = {
                "direction": rule["direction"],
                "ip_version": rule["ip_version"],
                "protocol": rule["protocol"]
            }

            if "port_min" in rule:
                new_rule["port_min"] = rule["port_min"]
            if "port_max" in rule:
                new_rule["port_max"] = rule["port_max"]

            # determine what kind of rule this is
            new_rule["remote"] = {}
            if "cidr_block" in rule["remote"]:
                new_rule["remote"]["cidr_block"] = rule["remote"]["cidr_block"]
            elif "address" in rule["remote"]:
                new_rule["remote"]["address"] = rule["remote"]["address"]
            elif "security_group" in rule["remote"]:
                # get remote security group id
                new_rule["remote"]["id"] = getsecuritygroupid(rule["remote"]["security_group"], vpcid)
            else:
                print("Invalid remote rule type (%s) for security group." % (rule["remote"]))
                quit()

            rules.append(new_rule)

        parms = {
            "name": security_group["security_group"],
            "rules": rules,
            "vpc": {"id": vpcid}
        }
        try:
            resp = requests.post(iaas_endpoint + '/v1/security_groups' + version, json=parms, headers=headers,
                                 timeout=30)
            resp.raise_for_status()
        except requests.exceptions.ConnectionError as errc:
            print("Error Connecting:", errc)
            quit()
        except requests.exceptions.Timeout as errt:
            print("Timeout Error:", errt)
            quit()
        except requests.exceptions.HTTPError as errb:
            if resp.status_code == 400:
                print("Invalid security_group template provided.")
                print("template: %s" % parms)
                print("Error Data: %s" % errb)
                quit()
            else:
                unknownapierror(resp)

        if resp.status_code == 201:
            security_group = resp.json()
            securitygroupid = security_group["id"]
            print("Security Group %s (%s) was created successfully." % (security_group["name"], securitygroupid))
        else:
            unknownapierror(resp)
    else:
        # Security group already exists.  do no recreate
        print("Security Group %s already exists." % (security_group["security_group"]))

    return securitygroupid


def getpublicgatewayid(zone_name, vpcid):
    #################################
    # Get Public Gateway ID
    #################################

    try:
        resp = requests.get(iaas_endpoint + '/v1/public_gateways' + version, headers=headers, timeout=30)
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
        public_gateways = json.loads(resp.content)["public_gateways"]
        # Determine if gateway exists and use it.  First get Gateways for this VPC
        public_gateway = list(filter(lambda gw: gw['vpc']['id'] == vpcid, public_gateways))
        # Determine if gateway exists in this vpc for this zone
        public_gateway = list(filter(lambda gw: gw['zone']['name'] == zone_name, public_gateway))
        if len(public_gateway) > 0:
            publicgatewayid = public_gateway[0]['id']
        else:
            publicgatewayid = None
    else:
        unknownapierror(resp)

    return publicgatewayid


def createpublicgateway(gateway_name, zone_name, vpcid):
    #################################
    # Create a public gateway
    #################################
    gatewayid = None

    parms = {
        "name": gateway_name,
        "zone": {"name": zone_name},
        "vpc": {"id": vpcid}
    }

    try:
        resp = requests.post(iaas_endpoint + '/v1/public_gateways' + version, json=parms, headers=headers, timeout=30)
        resp.raise_for_status()
    except requests.exceptions.ConnectionError as errc:
        print("Error Connecting:", errc)
        quit()
    except requests.exceptions.Timeout as errt:
        print("Timeout Error:", errt)
        quit()
    except requests.exceptions.HTTPError as errb:
        if resp.status_code == 400:
            print("Invalid public gateway template provided.")
            print("template: %s" % parms)
            print("Error Data: %s" % errb)
            quit()
        else:
            unknownapierror(resp)

    if resp.status_code == 201:
        gateway = resp.json()
        gatewayid = gateway["id"]
        print("Public Gateway %s (%s) was created successfully." % (gateway_name, gatewayid))
    else:
        unknownapierror(resp)

    return gatewayid


def getvpnid(vpn_name):
    ################################################
    ## Lookup VPN ID by name
    ################################################
    try:
        resp = requests.get(iaas_endpoint + '/v1/vpn_gateways' + version, headers=headers, timeout=30)
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
        vpn_gateways = json.loads(resp.content)["vpn_gateways"]
        vpn_gateway = \
            list(filter(lambda vpn_gateway: vpn_gateway["name"] == vpn_name, vpn_gateways))

        if len(vpn_gateway) > 0:
            vpn_gateway_id = vpn_gateway[0]['id']
        else:
            vpn_gateway_id = None
    else:
        unknownapierror(resp)

    return vpn_gateway_id


def createvpn(vpn, zone_address_prefix_cidr, subnet_id, resource_group_id):
    #################################
    # Create a VPN and connection
    #################################

    # Check if VPNaaS instance already exists

    vpn_id = getvpnid(vpn["name"])
    if vpn_id is None:

        parms = {
            "name": vpn["name"],
            "subnet": {"id": subnet_id},
            "resource_group": {"id": resource_group_id}
        }
        try:
            resp = requests.post(iaas_endpoint + '/v1/vpn_gateways' + version, json=parms, headers=headers, timeout=30)
            resp.raise_for_status()
        except requests.exceptions.ConnectionError as errc:
            print("Error Connecting:", errc)
            quit()
        except requests.exceptions.Timeout as errt:
            print("Timeout Error:", errt)
            quit()
        except requests.exceptions.HTTPError as errb:
            if resp.status_code == 400:
                print("Invalid VPN template provided.")
                print("template: %s" % parms)
                print("Error Data: %s" % errb)
                quit()
            else:
                unknownapierror(resp)


        if resp.status_code == 201:
            vpn_id = resp.json()["id"]
            print("VPN %s was created successfully." % (vpn["name"]))
        else:
            unknownapierror(resp)

        # now Create Connections to VPN
        if "connections" in vpn:
            for connection in vpn["connections"]:
                parms = {
                    "name": connection["name"],
                    "peer_address": connection["peer_address"],
                    "psk": connection["preshared_key"],
                    "local_cidrs": [zone_address_prefix_cidr],
                    "peer_cidrs": connection["peer_cidrs"]
                }

                try:
                    resp = requests.post(iaas_endpoint + '/v1/vpn_gateways/' + vpn_id + "/connections" + version,
                                         json=parms,
                                         headers=headers, timeout=30)
                    resp.raise_for_status()
                except requests.exceptions.ConnectionError as errc:
                    print("Error Connecting:", errc)
                    quit()
                except requests.exceptions.Timeout as errt:
                    print("Timeout Error:", errt)
                    quit()
                except requests.exceptions.HTTPError as errb:
                    if resp.status_code == 400:
                        print("Invalid VPN connection template provided.")
                        print("template: %s" % parms)
                        print("Error Data:  %s" % errb)
                        quit()
                    else:
                        unknownapierror(resp)

                if resp.status_code == 201:
                    vpn_connection = resp.json()
                    print("VPN connection %s was created successfully." % (connection["name"]))
                else:
                    unknownapierror(resp)
    else:
        print("VPN %s already exists in VPC." % (vpn["name"]))
    return vpn_id

def attachpublicgateway(gateway_id, subnet_id):
    #################################
    # Attach a public gateway
    #################################

    # Check subnet status first...waiting up to 30 seconds
    count = 0
    while count < 12:
        try:
            resp = requests.get(iaas_endpoint + '/v1/subnets/' + subnet_id + version, headers=headers, timeout=30)
            resp.raise_for_status()
        except requests.exceptions.ConnectionError as errc:
            print("Error Connecting:", errc)
            quit()
        except requests.exceptions.Timeout as errt:
            print("Timeout Error:", errt)
            quit()
        except requests.exceptions.HTTPError as errb:
            unknownapierror(resp)

        subnet_status = json.loads(resp.content)["status"]
        if subnet_status == "available":
            break
        else:
            print("Waiting for subnet creation before attaching public gateway.   Sleeping for 5 seconds...")
            count += 1
            time.sleep(5)

    parms = {"id": gateway_id}
    try:
        resp = requests.put(iaas_endpoint + '/v1/subnets/' + subnet_id + '/public_gateway' + version, json=parms,
                            headers=headers, timeout=30)
        resp.raise_for_status()
    except requests.exceptions.ConnectionError as errc:
        print("Error Connecting:", errc)
        quit()
    except requests.exceptions.Timeout as errt:
        print("Timeout Error:", errt)
        quit()
    except requests.exceptions.HTTPError as errb:
        if resp.status_code == 400:
            print("Public gateway could not be attached.")
            print("template: %s" % parms)
            print("Error Data:  %s" % errb)
            quit()
        elif resp.status_code == 404:
            print("A subnet with the specified identifier could not be found.")
            print("template: %s" % parms)
            print("Request: %s" % resp.request.url)
            print("Response:  %s" % json.loads(resp.content))
            print("Error Data:  %s" % errb)
            quit()
        else:
            unknownapierror(resp)

    if resp.status_code == 201:
        attach = resp.json()
        print("Public gateway attached to subnet %s." % attach["name"])
        return attach
    else:
        unknownapierror(resp)
    return


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


def createvpc(vpc_name, region, classic_access, resource_group_id, default_network_acl):
    ##################################
    # Create VPC in desired region
    ##################################

    # Check if VPC exists already by getting the VPC id
    vpcid = getvpcid(vpc_name)

    if getvpcid(vpc_name) is None:
        # VPC does not exist so proceed with creating it.

        if generation == 1:
            # Determine if network_acl name already exists and retreive id for use.
            default_network_acl_id = getnetworkaclid(default_network_acl)

            # create parameters for VPC creation Gen1
            parms = {"name": topology["vpc"],
                     "classic_access": topology["classic_access"],
                     "default_network_acl": {"id": default_network_acl_id},
                     "resource_group": {"id": resource_group_id}
                     }

        else:
            # create parameters for VPC creation Gen2
            parms = {"name": topology["vpc"],
                     "resource_group": {"id": resource_group_id}
                    }

        try:
            resp = requests.post(iaas_endpoint + '/v1/vpcs' + version, json=parms, headers=headers, timeout=30)
            resp.raise_for_status()
        except requests.exceptions.ConnectionError as errc:
            print("Error Connecting:", errc)
            quit()
        except requests.exceptions.Timeout as errt:
            print("Timeout Error:", errt)
            quit()
        except requests.exceptions.HTTPError as errb:
            if resp.status_code == 400:
                print("Invalid VPC template provided.")
                print("Response Code: %s" % (resp.status_code))
                print("Request Method: %s" % (resp.request.method))
                print("Request URL: %s" % (resp.request.url))
                print("Template: %s" % parms)
                print("Error Data:  %s" % errb)
                quit()
            else:
                unknownapierror(resp)

        if resp.status_code == 201:
            vpc = resp.json()
            vpcid = vpc["id"]
            print("Created VPC named %s (%s) in region %s." % (vpc_name, vpcid, region))
        else:
            unknownapierror(resp)
    else:
        print("The VPC named %s (%s) already exists in region." % (vpc_name, vpcid))

    return vpcid


def getaddressprefixid(vpcid, name):
    ################################################
    ## Lookup VPC-ADDRESS-PREFIX ID by name
    ################################################

    addressprefixid = None

    try:
        resp = requests.get(iaas_endpoint + '/v1/vpcs/' + vpcid + '/address_prefixes' + version, headers=headers,
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

    if resp.status_code == 200:
        prefixlist = json.loads(resp.content)["address_prefixes"]
        prefix_id = list(filter(lambda p: p['name'] == name, prefixlist))

        if len(prefix_id) > 0:
            if prefix_id[0]["id"] != 0:
                addressprefixid = prefix_id[0]["id"]
    else:
        unknownapierror(resp)

    return addressprefixid


def createaddressprefix(vpcid, zone, cidr):
    ################################################
    ## Create New Prefix in VPC
    ################################################

    # get list of prefixes in VPC to check if prefix already exists
    name = zone + "-address-prefix"
    addressprefixid = getaddressprefixid(vpcid, name)

    if addressprefixid is None:
        # address prefix doesn't exist.  Create it.

        parms = {"name": name,
                 "zone": {"name": zone},
                 "cidr": cidr
                 }
        try:
            resp = requests.post(iaas_endpoint + '/v1/vpcs/' + vpcid + '/address_prefixes' + version, json=parms,
                                 headers=headers, timeout=30)
            resp.raise_for_status()
        except requests.exceptions.ConnectionError as errc:
            print("Error Connecting:", errc)
            quit()
        except requests.exceptions.Timeout as errt:
            print("Timeout Error:", errt)
            quit()
        except requests.exceptions.HTTPError as errb:
            if resp.status_code == 400:
                print("An invalid prefix template provided.")
                print("template: %s" % parms)
                print("Error Data:  %s" % errb)
                quit()

            elif resp.status_code == 404:
                print("The specified VPC (%s) could not be found." % vpcid)
                print("template: %s" % parms)
                print("Error Data:  %s" % errb)
                quit()

            elif resp.status_code == 409:
                print("The prefix template conflicts with another prefix in this VPC.")
                print("template: %s" % parms)
                print("Error Data:  %s" % errb)
                quit()
            else:
                unknownapierror(resp)

        if resp.status_code == 201:
            addressprefixid = resp.json()["id"]
            print("New vpc-address-prefix %s (%s) %s was created successfully in zone %s." % (
                name, addressprefixid, cidr, zone))
        else:
            unknownapierror(resp)
    else:
        # Address Prefix already exists
        print("vpc-address-prefix %s (%s) already exists." % (name, addressprefixid))

    return addressprefixid


def getsubnetid(subnet_name):
    ################################################
    ## Lookup subnet id by name
    ################################################

    # get list of subnets in region

    try:
        resp = requests.get(iaas_endpoint + '/v1/subnets/' + version, headers=headers, timeout=30)
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
        subnetlist = json.loads(resp.content)["subnets"]
        subnet_id = list(filter(lambda s: s['name'] == subnet_name, subnetlist))
        if len(subnet_id) > 0:
            subnetid = subnet_id[0]["id"]
        else:
            subnetid = None
    else:
        unknownapierror(resp)

    return subnetid


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


def getinstanceid(instance_name, subnetid):
    ################################################
    ## Lookup instance id by name
    ################################################

    # get list of instances to check if instance already exists
    instanceid = None

    try:
        resp = requests.get(iaas_endpoint + '/v1/instances/' + version + "&network_interfaces.subnet.id=" + subnetid,
                            headers=headers, timeout=30)
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
        instancelist = json.loads(resp.content)["instances"]
        if len(instancelist) > 0:
            instancelist = list(filter(lambda i: i['name'] == instance_name, instancelist))
            if len(instancelist) > 0:
                instanceid = instancelist[0]["id"]
    else:
        unknownapierror(resp)

    return instanceid

def createinstance(zone_name, instance_name, vpc_id, image_id, profile_name, sshkey_id, subnet_id, security_group,
                   user_data, volumes):

    ##############################################
    # create new instance in desired vpc and zone
    ##############################################

    instanceid = getinstanceid(instance_name, subnet_id)

    if instanceid is None:

        parms = {"zone": {"name": zone_name},
                 "name": instance_name,
                 "vpc": {"id": vpc_id},
                 "image": {"id": image_id},
                 "profile": {"name": profile_name},
                 "keys": [{"id": sshkey_id}],
                 "primary_network_interface": {
                     "name": "eth0",
                     "subnet": {"id": subnet_id},
                     "security_groups": [{"id": getsecuritygroupid(security_group, vpc_id)}]},
                 "network_interfaces": [],
                 "volume_attachments": volumes,
                 "boot_volume_attachment": {
                     "volume": {
                         "capacity": 100,
                         "profile": {"name": "general-purpose"}
                     },
                     "delete_volume_on_instance_delete": True
                 },
                 "user_data": user_data
                 }


        try:
            resp = requests.post(iaas_endpoint + '/v1/instances' + version, json=parms, headers=headers, timeout=30)
            resp.raise_for_status()
        except requests.exceptions.ConnectionError as errc:
            print("Error Connecting:", errc)
            quit()
        except requests.exceptions.Timeout as errt:
            print("Timeout Error:", errt)
            quit()
        except requests.exceptions.HTTPError as errb:
            if resp.status_code == 400:
                print("Invalid instance template provided.")
                print("template: %s" % parms)
                print("Error Data:  %s" % errb)
                print("error detail: %s" %  resp.text)
                quit()
            else:
                print(json.dumps(parms, indent=4))
                unknownapierror(resp)

        if resp.status_code == 201:
            instance = resp.json()
            instanceid = instance["id"]
            print("Created %s (%s) instance successfully." % (instance_name, instanceid))
        else:
            unknownapierror(resp)

    else:
        # Instance already exists do not create and return id.
        print('Instance named %s (%s) already exists in subnet.' % (
            instance_name, instanceid))
    return instanceid

def assignfloatingip(instance_id):
    ##############################################
    # Assign Floating IP to instance
    ##############################################

    # Verify instance provisioning complete
    while True:
        try:
            resp = requests.get(iaas_endpoint + '/v1/instances/' + instance_id + version, headers=headers, timeout=30)
            resp.raise_for_status()
        except requests.exceptions.ConnectionError as errc:
            print("Error Connecting:", errc)
            quit()
        except requests.exceptions.Timeout as errt:
            print("Timeout Error:", errt)
            quit()
        except requests.exceptions.HTTPError as errb:
            unknownapierror(resp)

        instance_status = json.loads(resp.content)
        if "status" in instance_status:
            if instance_status["status"] == "running":
                network_interface = instance_status["primary_network_interface"]["id"]
                break
            else:
                print("Waiting for instance creation to complete.   Sleeping for 5 seconds...")
                time.sleep(5)
        else:
            print("Waiting for instance creation to complete.   Sleeping for 5 seconds...")
            time.sleep(5)

    # Check if floating IP already assigned
    try:
        resp = requests.get(
            iaas_endpoint + "/v1/instances/" + instance_id + "/network_interfaces/" + network_interface + "/floating_ips" + version,
            headers=headers, timeout=30)
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
        floating_ip = json.loads(resp.content)
        if "floating_ips" in floating_ip:
            floating_ip = floating_ip["floating_ips"]
            if len(floating_ip) > 0:
                print("Floating ip %s (%s) is already assigned to %s." % (
                    floating_ip[0]["address"], floating_ip[0]['id'], instance_status["name"]))
                return floating_ip[0]['id'], floating_ip[0]['address']

    #  None assigned.  Request one.
    parms = {
        "target": {
            "id": network_interface
        }
    }

    try:
        resp = requests.post(iaas_endpoint + '/v1/floating_ips' + version, json=parms, headers=headers, timeout=30)
        resp.raise_for_status()
    except requests.exceptions.ConnectionError as errc:
        print("Error Connecting:", errc)
        quit()
    except requests.exceptions.Timeout as errt:
        print("Timeout Error:", errt)
        quit()
    except requests.exceptions.HTTPError as errb:
        if resp.status_code == 400:
            print("Invalid template provided.")
            print("template: %s" % parms)
            print("Error Data: %s" % errb)
            quit()
        else:
            unknownapierror(resp)

    if resp.status_code == 201:
        floating_ip = resp.json()
        print("Floating_ip %s assigned to %s successfully." % (floating_ip["address"], instance_status["name"]))
        return floating_ip['id'], floating_ip['address']
    else:
        unknownapierror(resp)
    return


def createloadbalancer(lb, resource_group_id):
    ################################################
    ## create LB instance
    ################################################

    # get list of load balancers to check if instance already exists
    try:
        resp = requests.get(iaas_endpoint + '/v1/load_balancers/' + version,
                            headers=headers, timeout=30)
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
        lblist = json.loads(resp.content)["load_balancers"]
        if len(lblist) > 0:
            lblist = list(filter(lambda i: i['name'] == lb["lbInstance"], lblist))
            if len(lblist) > 0:
                print('Load Balancer named %s (%s) already exists in subnet.' % (
                    lb["lbInstance"], lblist[0]["id"]))
                return lblist[0]["id"]
    else:
        unknownapierror(resp)

    # Create ListenerTemplate for use in creating load balancer
    listenerTemplate = []
    for listener in lb["listeners"]:
        listener = {
            "port": listener["port"],
            "protocol": listener["protocol"],
            "default_pool": {"name": listener["default_pool_name"]},
            "connection_limit": listener["connection_limit"]
        }
        listenerTemplate.append(listener)

    # Create pool template for use in creating load balancer
    poolTemplate = []

    # create multiple pools
    for pool in lb["pools"]:

        # Create Heath Monitor Template for Pool
        healthMonitorTemplate = {"type": pool["health_monitor"]["type"],
                                 "delay": pool["health_monitor"]["delay"],
                                 "max_retries": pool["health_monitor"]["max_retries"],
                                 "timeout": pool["health_monitor"]["timeout"],
                                 "url_path": pool["health_monitor"]["url_path"]
                                 }

        # Determine whuch members are in this pool
        memberTemplate = []
        for zone in topology["zones"]:
            for subnet in zone["subnets"]:
                # get list of instances on subnet.

                try:
                    resp = requests.get(
                        iaas_endpoint + '/v1/instances/' + version + "&network_interfaces.subnet.name=" + subnet[
                            "name"], headers=headers, timeout=30)
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
                    instancelist = json.loads(resp.content)["instances"]

                if "instances" in subnet:
                    for instance in subnet["instances"]:
                        if "in_lb_pool" in instance:
                            # Check if this instance is marked for this LB and pool and if so append instances to member template
                            if len(instancelist) > 0:
                                for in_lb_pool in instance["in_lb_pool"]:
                                    if (in_lb_pool["lb_name"] == lb["lbInstance"]) and (
                                            in_lb_pool["lb_pool"] == pool["name"]):
                                        # iterate through quantity to find each instance
                                        for count in range(1, instance["quantity"] + 1):
                                            name = (instance["name"] % count) + "-" + zone["name"]
                                            # search by instance name to get ipv4 address, and add as member.
                                            instanceinfo = list(filter(lambda i: i['name'] == name, instancelist))
                                            if len(instanceinfo) > 0:
                                                memberTemplate.append({"port": in_lb_pool["listen_port"],
                                                                       "target": {"address": instanceinfo[0][
                                                                           "primary_network_interface"][
                                                                           "primary_ipv4_address"]},
                                                                       "weight": 100})
        # sessions persistence specified for pool add to pool template.
        if "session_persistence" in pool:
            session_persistence = pool["session_persistence"]
        else:
            session_persistence = ""

        poolTemplate.append({
            "algorithm": pool["algorithm"],
            "health_monitor": healthMonitorTemplate,
            "name": pool["name"],
            "protocol": pool["protocol"],
            "sessions_persistence": session_persistence,
            "members": memberTemplate
        })

        # get subnet id's for load balancer creationer
        subnet_list = []
        for subnet in lb['subnets']:
            # get list of subnets in region to check if subnet already exists

            try:
                resp = requests.get(iaas_endpoint + '/v1/subnets/' + version, headers=headers, timeout=30)
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
                subnetlist = json.loads(resp.content)["subnets"]
                subnet_id = list(filter(lambda s: s['name'] == subnet, subnetlist))
                if len(subnet_id) > 0:
                    subnet = {"id": subnet_id[0]["id"]}
                    subnet_list.append(subnet)

    # Build load balancer using templates just created.
    parms = {"name": lb["lbInstance"],
             "is_public": lb['is_public'],
             "subnets": subnet_list,
             "listeners": listenerTemplate,
             "pools": poolTemplate,
             "resource_group": {"id": resource_group_id}
             }

    try:
        resp = requests.post(iaas_endpoint + '/v1/load_balancers' + version, json=parms, headers=headers, timeout=30)
        resp.raise_for_status()
    except requests.exceptions.ConnectionError as errc:
        print("Error Connecting:", errc)
        quit()
    except requests.exceptions.Timeout as errt:
        print("Timeout Error:", errt)
        quit()
    except requests.exceptions.HTTPError as errb:
        if resp.status_code == 400:
            print("Invalid instance template provided.")
            print("template: %s" % parms)
            print("Error Data:  %s" % errb)
            quit()
        else:
            unknownapierror(resp)


    if resp.status_code == 201:
        load_balancer = resp.json()
        print("Created %s (%s) load balancer successfully." % (lb["lbInstance"], load_balancer["id"]))
        return (load_balancer["id"])
    else:
        unknownapierror(resp)
    return

def encodecloudinit(filename):
    ################################################
    ## encode cloud-init.txt for use with user data
    ################################################
    combined_message = MIMEMultipart()
    with open(filename) as fh:
        contents = fh.read()
    sub_message = MIMEText(contents, "cloud-config", sys.getdefaultencoding())
    sub_message.add_header('Content-Disposition', 'inline; filename="%s"' % (filename))
    combined_message.attach(sub_message)
    #return str(combined_message).encode()
    return str(combined_message)

def getinstancetemplate(templates, search):
    ################################################
    ## Find instance template in list
    ################################################

    template = [d for d in templates if d['template'] == search]
    return template[0]

def getimageid(image_name):
    ################################################
    ## Return the image_id of an image name
    ################################################

    try:
        resp = requests.get(iaas_endpoint + '/v1/images/' + version, headers=headers, timeout=30)
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
        imagelist = json.loads(resp.content)["images"]
        image_id = list(filter(lambda i: i['name'] == image_name, imagelist))
        if len(image_id) > 0:
            return image_id[0]["id"]
        else:
            return 0

def getresourcegroupid(resource_group):
    ################################################
    ## Return the resource group id of resource group
    ################################################

    try:
        resp = requests.get(resource_controller_endpoint + '/v2/resource_groups', headers=headers, timeout=30)
        resp.raise_for_status()
    except requests.exceptions.ConnectionError as errc:
        print("Error Connecting:", errc)
        quit()
    except requests.exceptions.Timeout as errt:
        print("Timeout Error:", errt)
        quit()
    except requests.exceptions.HTTPError as errb:
        if resp.status_code == 401:
            print("Your access token is invalid or authentication of your token failed.")
            quit()
        elif resp.status_code == 403:
            print("Your access token is valid but does not have then necessary permissions to access this resource.")
            quit()
        elif resp.status_code == 429:
            print("Too many requests.  Please wait a few minutes and try again.")
            quit()
        else:
            unknownapierror(resp)

    if resp.status_code == 200:
        resources = json.loads(resp.content)["resources"]

        resource = list(filter(lambda i: i['name'] == resource_group, resources))
        if len(resource) > 0:
            resource_id = resource[0]["id"]
            return resource_id
        else:
            return None
    else:
        unknownapierror(resp)
    return resource_id

def getsshkeyid(sshkey_name):
    ################################################
    ## Return the sshkey_id of an sshkey name
    ################################################

    try:
        resp = requests.get(iaas_endpoint + '/v1/keys/' + version, headers=headers, timeout=30)
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
        keylist = json.loads(resp.content)["keys"]
        sshkey_id = list(filter(lambda k: k['name'] == sshkey_name, keylist))
        if len(sshkey_id) > 0:
            sshkeyid = sshkey_id[0]["id"]
        else:
            sshkeyid = None

    else:
        unknownapierror(resp)

    return sshkeyid

def createsshkey(sshkey):
    ################################################
    ## Create new ssshkey
    ################################################

    # Check if key already exists
    sshkeyid = getsshkeyid(sshkey["sshkey"])
    if sshkeyid is None:
        # Does not exists so create a new key

        parms = {"name": sshkey["sshkey"],
                 "public_key": sshkey["public_key"],
                 "type": "rsa"
                 }
        try:
            resp = requests.post(iaas_endpoint + '/v1/keys' + version, json=parms, headers=headers, timeout=30)
            resp.raise_for_status()
        except requests.exceptions.ConnectionError as errc:
            print("Error Connecting:", errc)
            quit()
        except requests.exceptions.Timeout as errt:
            print("Timeout Error:", errt)
            quit()
        except requests.exceptions.HTTPError as errb:
            if resp.status_code == 400:
                print("Invalid sshkey template provided.")
                print("template: %s" % parms)
                print("Error Data: %s" % errb)
                quit()
            else:
                unknownapierror(resp)

        if resp.status_code == 201:
            print("SSH Key named %s created." % (sshkey["sshkey"]))
            sshkeyid = json.loads(resp.content)["id"]
        else:
            unknownapierror(resp)

    else:
        print("SSH Key %s already exists." % sshkey["sshkey"])

    return sshkeyid


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


#####################################
# Set Global Variables
#####################################

iaas_endpoint = "https://us-south.iaas.cloud.ibm.com"
resource_controller_endpoint = "https://resource-controller.cloud.ibm.com"
version = "?version=2019-05-31"


#####################################
# Read desired topology YAML file
#####################################

parser = argparse.ArgumentParser(description="Create VPC topology.")
parser.add_argument("-y", "--yaml", help="YAML based topology file to create")
parser.add_argument("-k", "--apikey", help="File which contains apikey.")

args = parser.parse_args()
if args.yaml is None:
    filename = "topology.yaml"
else:
    filename = args.yaml

if args.apikey is None:
    ini_file = "provision-vpc.ini"
else:
    ini_file = args.apikey

# Read INI file and get bearer IAM token
apikey = parse_apiconfig(ini_file)
headers = getiamtoken(apikey)

with open(filename, 'r') as stream:
    topology = yaml.load(stream, Loader=yaml.FullLoader)[0]

# Get the preferred VPC generation to use, default to gen 1 if not specified
if 'generation' in topology.keys():
    generation = topology["generation"]
else:
    generation = 1

# Determine if region identified is available and get endpoint to use

version = version + '&generation=' + str(generation)
region = getregionavailability(topology["region"])

if region["status"] == "available":
    iaas_endpoint = region["endpoint"]
    if generation == 2:
        # override endpoint and force us-south until GA
        iaas_endpoint = "https://us-south.iaas.cloud.ibm.com"
    print("Provisioning VPC using generation %d" % generation)
    print("Using VPC endpoint %s" % iaas_endpoint)
    main(region["name"], generation, topology)
else:
    print("Region %s is not currently available." % region["name"])
    quit()
