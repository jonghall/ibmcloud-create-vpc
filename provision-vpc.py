#!/usr/bin/env python3
## Provision-vpc - A script to provision vpc, network, and compute resources based on a templated topology yaml file.
## Author: Jon Hall
##

## Latest Next Gen API Spec: https://pages.github.ibm.com/riaas/api-spec/spec_genesis_2019-06-04/

import requests, json, time, sys, yaml, argparse
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText


def main(region):
    # Get Zones for specified region
    zones = getzones(topology['region'])

    for zone in zones:
        print("Zone %s is available in region %s" % (zone["name"], region))

    # Create Network acls
    for network_acl in topology["network_acls"]:
        createnetworkacl(network_acl)

    # Create VPC
    vpc_name = topology["vpc"]
    vpc_id = createvpc()

    # Create vpc-address-prefixes
    for prefix in topology['address_prefix']:
        createaddressprefix(vpc_id, prefix['name'], prefix['zone'], prefix['cidr'])

    # Create VPC's security groups
    for security_group in topology['security_groups']:
        createsecuritygroup(security_group, vpc_id)

    # Create sshKeys for VPC
    for sshkey in topology["sshkeys"]:
        createsshkey(sshkey)

    #######################################################################
    # Iterate through subnets in each zone and create subnets & instances
    #######################################################################

    for zone in topology["zones"]:
        for subnet in zone["subnets"]:
            ## Provision new Subnet
            subnet_id = createsubnet(vpc_id, zone["name"], subnet)

            # Check if Public Gateway is required by Subnet
            if 'publicGateway' in subnet:
                if subnet["publicGateway"]:
                    # A gateway is needed check if Public Gateway already exists in zone, if not create.
                    resp = requests.get(rias_endpoint + '/v1/public_gateways' + version, headers=headers)
                    if resp.status_code == 200:
                        public_gateways = json.loads(resp.content)["public_gateways"]
                        # Determine if gateway exists and use it.  First get Gateways for this VPC
                        public_gateway = list(filter(lambda gw: gw['vpc']['id'] == vpc_id, public_gateways))
                        # Determine if gateway exists in this vpc for this zone
                        public_gateway = list(filter(lambda gw: gw['zone']['name'] == zone["name"], public_gateway))

                        if len(public_gateway) > 0:
                            # gateway already exists, get it's ID and attach to subnet.
                            attachpublicgateway(public_gateway[0]["id"], subnet_id)
                        else:
                            # Does not exists, so need to create public gateway then attach
                            gateway_name = vpc_name + "-" + zone["name"] + "-gw"
                            public_gateway_id = createpublicgateway(gateway_name, zone["name"], vpc_id)
                            attachpublicgateway(public_gateway_id, subnet_id)
                    else:
                        print("%s Error getting list of gateways for zone %z." % (resp.status_code, zone["name"]))
                        print("Error Data:  %s" % json.loads(resp.content)['errors'])
                        quit()

            # Build instances for this subnet (if defined in topology)
            if "instances" in subnet:
                for instance in subnet["instances"]:
                    template = getinstancetemplate(topology["instanceTemplates"], instance["template"])
                    profile_name = template["profile_name"]
                    sshkey_name = template["sshkey"]
                    security_group = instance["security_group"]
                    bandwidth = template["bandwidth"]
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
                        instance_name = (instance["name"] % q)
                        volumes = []
                        for r in range(len(template["volumes"])):
                            volume = {
                                "volume": {
                                    "name": instance_name + '-secondary-vol-' + str(r),
                                    "capacity": template["volumes"][r]["capacity"],
                                    "iops": template["volumes"][r]["iops"],
                                    "profile": {
                                        "name": "general-purpose"}
                                },
                                "delete_volume_on_instance_delete": True
                            }
                            volumes.append(volume);
                        instance_id = createinstance(zone["name"], instance_name, vpc_id, image_id, profile_name,
                                                     sshkey_id,
                                                     subnet_id,
                                                     security_group,
                                                     user_data,
                                                     bandwidth,
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
            lb_id = createloadbalancer(lb)

    return


def getzones(region):
    #############################
    # Get list of zones in Region
    #############################

    resp = requests.get(rias_endpoint + '/v1/regions/' + region + '/zones' + version, headers=headers)
    if resp.status_code == 200:
        zones = json.loads(resp.content)["zones"]
        if len(zones) > 0:
            print("There are %s zones in the %s region" % (len(zones), region))
            return (zones)
        else:
            print("There are no zones available in this region.")
            quit()
    else:
        print("%s Error getting zones for region %s." % (resp.status_code, region))
        print("Error Data:  %s" % json.loads(resp.content)['errors'])
        quit()
    return


def getregionavailability(region):
    #############################
    # Get Region Availability
    #############################

    resp = requests.get(rias_endpoint + '/v1/regions/' + region + version, headers=headers)

    if resp.status_code == 200:
        region = json.loads(resp.content)

        if topology["region"] == region["name"] and region["status"] == "available":
            print("Region %s region is available." % region["name"])
            return region
        else:
            print('Desired region is not currently available.')
            quit()
    else:
        print("%s Error getting details on region %s." % (resp.status_code, topology['region']))
        print("Error Data:  %s" % json.loads(resp.content)['errors'])
        quit()
    return


def getnetworkaclid(network_acl_name):
    ################################################
    ## Lookup network acl id by name
    ################################################

    resp = requests.get(rias_endpoint + '/v1/network_acls/' + version, headers=headers)
    if resp.status_code == 200:
        acls = json.loads(resp.content)["network_acls"]
        default_network_acl = \
            list(filter(lambda acl: acl['name'] == network_acl_name, acls))

        if len(default_network_acl) > 0:
            network_acl_id = default_network_acl[0]['id']
        else:
            network_acl_id = 0
    else:
        network_acl_id = 0

    return (network_acl_id)


def getsecuritygroupid(security_group, vpc_id):
    ################################################
    ## Lookup security group id by name
    ################################################

    resp = requests.get(rias_endpoint + '/v1/security_groups/' + version + "&vpc.if=" + vpc_id, headers=headers)
    if resp.status_code == 200:
        sgs = json.loads(resp.content)["security_groups"]
        default_security_group = \
            list(filter(lambda acl: acl['name'] == security_group, sgs))

        if len(default_security_group) > 0:
            security_group_id = default_security_group[0]['id']
        else:
            security_group_id = 0
    else:
        security_group_id = 0

    return security_group_id


def createnetworkacl(network_acl):
    ################################################
    ## create network acl
    ################################################

    # check if ACL already exists by checking for id
    if getnetworkaclid(network_acl["network_acl"]) == 0:
        # Network ACLS does not exist create it

        rules = []
        for rule in network_acl['rules']:
            new_rule = {
                "name": rule["name"],
                "action": rule["action"],
                "direction": rule["direction"],
                "source": rule["source"],
                "destination": rule["destination"]
            }

            if "port_min" in rule:
                new_rule["port_min"] = rule["port_min"]
            if "port_max" in rule:
                new_rule["port_max"] = rule["port_max"]

            rules.append(new_rule)

        parms = {
            "name": network_acl["network_acl"],
            "rues": rules,
        }

        resp = requests.post(rias_endpoint + '/v1/network_acls' + version, json=parms, headers=headers)

        if resp.status_code == 201:
            network_acl = resp.json()
            print("Network ACL %s (%s) was created successfully." % (network_acl["name"], network_acl["id"]))
            return
        elif resp.status_code == 400:
            print("Invalid network_acl template provided.")
            print("template=%s" % json.dumps(parms, indent=4))
            print("Error Data:  %s" % json.loads(resp.content)['errors'])
            quit()
        else:
            # error stop execution
            print("%s Error creating network acls." % (resp.status_code, zone_name))
            print("template=%s" % parms)
            print("Error Data:  %s" % json.loads(resp.content)['errors'])
            quit()
    else:
        # Network ACL already exists.  do no recreate
        print("Network ACL %s already exists." % (network_acl["network_acl"]))
        return


def createsecuritygroup(security_group, vpc_id):
    ################################################
    ## create security group
    ################################################

    # check if security group already exists by checking for id
    if getsecuritygroupid(security_group["security_group"], vpc_id) == 0:
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
                new_rule["remote"]["id"] = getsecuritygroupid(rule["remote"]["security_group"], vpc_id)
            else:
                print("Invalid remote rule type (%s) for security group." % (rule["remote"]))
                quit()

            rules.append(new_rule)

        parms = {
            "name": security_group["security_group"],
            "rules": rules,
            "vpc": {"id": vpc_id}
        }
        resp = requests.post(rias_endpoint + '/v1/security_groups' + version, json=parms, headers=headers)

        if resp.status_code == 201:
            security_group = resp.json()
            print("Security Group %s (%s) was created successfully." % (security_group["name"], security_group["id"]))
            return
        elif resp.status_code == 400:
            print("Invalid security_group template provided.")
            print("template=%s" % parms)
            print("Error Data:  %s" % json.loads(resp.content)['errors'])
            quit()
        else:
            # error stop execution
            print("%s Error creating security group." % (resp.status_code))
            print("template=%s" % parms)
            print("Error Data:  %s" % json.loads(resp.content)['errors'])
            quit()
    else:
        # Security group already exists.  do no recreate
        print("Security Group %s already exists." % (security_group["security_group"]))
        return


def createpublicgateway(gateway_name, zone_name, vpc_id):
    #################################
    # Create a public gateway
    #################################

    parms = {
        "name": gateway_name,
        "zone": {"name": zone_name},
        "vpc": {"id": vpc_id}
    }
    resp = requests.post(rias_endpoint + '/v1/public_gateways' + version, json=parms, headers=headers)

    if resp.status_code == 201:
        gateway = resp.json()
        print("Public Gateway %s named %s was created successfully." % (gateway["id"], gateway_name))
        return (gateway["id"])
    elif resp.status_code == 400:
        print("Invalid public gateway template provided.")
        print("template=%s" % parms)
        print("Error Data:  %s" % json.loads(resp.content)['errors'])
        quit()
    else:
        # error stop execution
        print("%s Error creating public gateway." % (resp.status_code, zone_name))
        print("template=%s" % parms)
        print("Error Data:  %s" % json.loads(resp.content)['errors'])
        quit()
    return


def attachpublicgateway(gateway_id, subnet_id):
    #################################
    # Attach a public gateway
    #################################

    # Check subnet status first...waiting up to 30 seconds
    count = 0
    while count < 12:
        resp = requests.get(rias_endpoint + '/v1/subnets/' + subnet_id + version, headers=headers);
        subnet_status = json.loads(resp.content)["status"]
        if subnet_status == "available":
            break
        else:
            print("Waiting for subnet creation before attaching public gateway.   Sleeping for 5 seconds...")
            count += 1
            time.sleep(5)

    parms = {"id": gateway_id}
    resp = requests.put(rias_endpoint + '/v1/subnets/' + subnet_id + '/public_gateway' + version, json=parms,
                        headers=headers)
    if resp.status_code == 201:
        attach = resp.json()
        print("Public gateway attached to subnet %s." % attach["name"])
        return attach
    elif resp.status_code == 400:
        print("Public gateway could not be attached.")
        print("template=%s" % parms)
        print("Error Data:  %s" % json.loads(resp.content)['errors'])
        quit()
    elif resp.status_code == 404:
        print("A subnet with the specified identifier could not be found.")
        print("template=%s" % parms)
        print("Request: %s" % rias_endpoint + '/v1/subnets/' + subnet_id + '/public_gateway' + version)
        print("Response:  %s" % json.loads(resp.content))
        quit()
    else:
        # error stop execution
        print("%s Error attaching pubic gateway." % (resp.status_code, zone["name"]))
        print("template=%s" % parms)
        print("Error Data:  %s" % json.loads(resp.content)['errors'])
        quit()

    return


def createvpc():
    ##################################
    # Create VPC in desired region
    ##################################

    # get list of VPCs in region to check if VPC already exists
    resp = requests.get(rias_endpoint + '/v1/vpcs/' + version, headers=headers)
    if resp.status_code == 200:
        vpcs = json.loads(resp.content)["vpcs"]
        # Determine if network_acl name already exists and retreive id.
        vpc = list(filter(lambda vpc: vpc['name'] == topology["vpc"], vpcs))
        if len(vpc) > 0:
            print("The VPC named %s (%s) already exists in region." % (vpc[0]["name"], vpc[0]['id']))
            # NG doesn't have network_acls at this time
            if generation == 1: default_network_acl_id = getnetworkaclid(topology["default_network_acl"])
            vpc = vpc[0]
            return (vpc['id'])
        else:
            # VPC does not exist so proceed with creating it.

            # Determine if network_acl name already exists and retreive id for use.
            if generation == 1: resp = requests.get(rias_endpoint + '/v1/network_acls/' + version, headers=headers)
            if generation > 1 or resp.status_code == 200:
                if generation == 1:
                    acls = json.loads(resp.content)["network_acls"]
                    default_network_acl = \
                        list(filter(lambda acl: acl['name'] == topology["default_network_acl"], acls))

                if generation > 1 or len(default_network_acl) > 0:
                    if generation == 1:
                        print("%s network_acl already exists.   Using network_acl_id %s as default." % (
                            default_network_acl[0]["name"], default_network_acl[0]['id']))
                        default_network_acl_id = default_network_acl[0]['id']

                    # create parameters for VPC creation
                    if generation == 1:
                        parms = {"name": topology["vpc"],
                                 "classic_access": topology["classic_access"],
                                 "default_network_acl": {"id": default_network_acl_id}
                                 }
                    else:
                        parms = {"name": topology["vpc"]}

                    resp = requests.post(rias_endpoint + '/v1/vpcs' + version, json=parms, headers=headers)

                    if resp.status_code == 201:
                        vpc = resp.json()
                        print("Created VPC named %s (%s) in region %s." % (vpc['name'], vpc['id'], topology["region"]))
                        return (vpc["id"])
                    elif resp.status_code == 400:
                        print("Invalid VPC template provided.")
                        print("template=%s" % parms)
                        print("Error Data:  %s" % json.loads(resp.content)['errors'])
                        quit()
                    else:
                        # error stop execution
                        print(json.dumps(parms, indent=4))
                        print("%s Error." % resp.status_code)
                        print("Error Data:  %s" % json.loads(resp.content)['errors'])
                        quit()
                else:
                    # *** need to create default ACL if it doesn't exist already per rules in topology file.
                    print("%s network_acls does not exists.  Please manually implement and re-run." % (
                        topology["default_network_acl"]))
                    quit()
            else:
                # error stop execution
                print("%s Error getting acls for region %s." % (resp.status_code, topology['region']))
                quit()
    else:
        # error stop execution
        print("%s Error getting list of vpcs for region %s." % (resp.status_code, topology['region']))
        quit()
    return


def createaddressprefix(vpc_id, name, zone, cidr):
    ################################################
    ## Create New Prefix in VPC
    ################################################

    # get list of prefixes in VPC to check if prefix already exists
    resp = requests.get(rias_endpoint + '/v1/vpcs/' + vpc_id + '/address_prefixes' + version, headers=headers)
    if resp.status_code == 200:
        prefixlist = json.loads(resp.content)["address_prefixes"]
        prefix_id = list(filter(lambda p: p['name'] == name, prefixlist))
        if len(prefix_id) > 0:
            if prefix_id[0]["id"] != 0:
                print("Prefix named %s (%s) already exists in VPC." % (name, prefix_id[0]["id"]))
                return prefix_id[0]["id"]

    parms = {"name": name,
             "zone": {"name": zone},
             "cidr": cidr
             }

    resp = requests.post(rias_endpoint + '/v1/vpcs/' + vpc_id + '/address_prefixes' + version, json=parms,
                         headers=headers)

    if resp.status_code == 201:
        prefix_id = resp.json()["id"]
        print("New vpc-address-prefix %s named %s was created successfully in zone %s." % (
            cidr, name, zone))
        return prefix_id
    elif resp.status_code == 400:
        print("An invalid prefix template provided.")
        print("template=%s" % parms)
        print("Error Data:  %s" % json.loads(resp.content)['errors'])
        quit()
    elif resp.status_code == 404:
        print("The specified VPC (%s) could not be found." % vpc_id)
        print("template=%s" % parms)
        quit()
    elif resp.status_code == 409:
        print("The prefix template conflicts with another prefix in this VPC.")
        print("template=%s" % parms)
        quit()
    else:
        # error stop execution
        print("%s Error creating vpc-address-prefix in %s zone." % (resp.status_code, zone))
        print("template=%s" % parms)
        print("Error Data:  %s" % json.loads(resp.content)['errors'])
        quit()
    return


def createsubnet(vpc_id, zone_name, subnet):
    ################################################
    ## Create new subnet is zone
    ################################################

    # get list of subnets in region to check if subnet already exists
    resp = requests.get(rias_endpoint + '/v1/subnets/' + version, headers=headers)
    if resp.status_code == 200:
        subnetlist = json.loads(resp.content)["subnets"]
        subnet_id = list(filter(lambda s: s['name'] == subnet["name"], subnetlist))
        if len(subnet_id) > 0:
            print("Subnet named %s (%s) already exists in zone. " % (subnet["name"], subnet_id[0]["id"]))
            return subnet_id[0]["id"]

    network_acl_id = getnetworkaclid(subnet['network_acl'])

    if generation == 1 and network_acl_id == 0:
        print("Network ACL named %s does not exists" % (subnet['network_acl']))
        quit()

    parms = {"name": subnet["name"],
             "ipv4_cidr_block": subnet['ipv4_cidr_block'],
             "zone": {"name": zone_name},
             "vpc": {"id": vpc_id}
             }
    if generation == 1: parms["network_acl"] = {"id": network_acl_id},
    resp = requests.post(rias_endpoint + '/v1/subnets' + version, json=parms, headers=headers)

    if resp.status_code == 201:
        print("Subnet named %s requested in zone %s." % (subnet["name"], zone_name))
        newsubnet = resp.json()
        count = 0
        while count < 12:
            resp = requests.get(rias_endpoint + '/v1/subnets/' + newsubnet["id"] + version, headers=headers);
            subnet_status = json.loads(resp.content)["status"]
            if subnet_status == "available":
                break
            else:
                print(
                    "Waiting for subnet creation to complete before proceeding.   Sleeping for 5 seconds...")
                count += 1
                time.sleep(5)
        print("Subnet %s named %s was created successfully in zone %s." % (
            newsubnet["id"], subnet["name"], zone_name))
        return newsubnet["id"]
    elif resp.status_code == 400:
        print("Invalid subnet template provided.")
        print("template=%s" % parms)
        print("Error Data:  %s" % json.loads(resp.content)['errors'])
        quit()
    elif resp.status_code == 409:
        print("The subnet template conflicts with another subnet in this VPC.")
        print("template=%s" % parms)
        quit()
    else:
        # error stop execution
        print("%s Error creating subnet in %s zone." % (resp.status_code, zone["name"]))
        print("template=%s" % parms)
        print("Error Data:  %s" % json.loads(resp.content)['errors'])
        quit()
    return


def createinstance(zone_name, instance_name, vpc_id, image_id, profile_name, sshkey_id, subnet_id, security_group,
                   user_data, bandwidth, volumes):
    ##############################################
    # create new instance in desired vpc and zone
    ##############################################

    # get list of instances to check if instance already exists
    resp = requests.get(rias_endpoint + '/v1/instances/' + version + "&network_interfaces.subnet.id=" + subnet_id,
                        headers=headers)
    if resp.status_code == 200:
        instancelist = json.loads(resp.content)["instances"]
        if len(instancelist) > 0:
            instancelist = list(filter(lambda i: i['name'] == instance_name, instancelist))
            if len(instancelist) > 0:
                print('Instance named %s (%s) already exists in subnet.' % (
                    instance_name, instancelist[0]["id"]))
                return instancelist[0]["id"]
    else:
        # error stop execution
        print("%s Error querying subnet API to find if instance already exists." % (resp.status_code))
        print("Error Data:  %s" % json.loads(resp.content)['errors'])
        quit()

    parms = {"zone": {"name": zone_name},
             "name": instance_name,
             "vpc": {"id": vpc_id},
             "image": {"id": image_id},
             "user_data": user_data,
             "profile": {"name": profile_name},
             "keys": [{"id": sshkey_id}],
             "bandwidth": bandwidth,
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
             }
             }

    resp = requests.post(rias_endpoint + '/v1/instances' + version, json=parms, headers=headers)

    if resp.status_code == 201:
        instance = resp.json()
        print("Created %s (%s) instance successfully." % (instance["name"], instance["id"]))
        return (instance['id'])
    elif resp.status_code == 400:
        print("Invalid instance template provided.")
        print("template=%s" % parms)
        print("Error Data:  %s" % json.loads(resp.content)['errors'])
        quit()
    else:
        # error stop execution
        print(parms)
        print("%s Error creating instance." % resp.status_code)
        print("Error Data:  %s" % json.loads(resp.content)['errors'])
        quit()
    return


def assignfloatingip(instance_id):
    ##############################################
    # Assign Floating IP to instance
    ##############################################

    # Verify instance provisioning complete
    while True:
        resp = requests.get(rias_endpoint + '/v1/instances/' + instance_id + version, headers=headers);
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
    resp = requests.get(
        rias_endpoint + "/v1/instances/" + instance_id + "/network_interfaces/" + network_interface + "/floating_ips" + version,
        headers=headers)
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
    resp = requests.post(rias_endpoint + '/v1/floating_ips' + version, json=parms, headers=headers)

    if resp.status_code == 201:
        floating_ip = resp.json()
        print("Floating_ip %s assigned to %s successfully." % (floating_ip["address"], instance_status["name"]))
        return floating_ip['id'], floating_ip['address']
    elif resp.status_code == 400:
        print("Invalid template provided.")
        print("template=%s" % parms)
        print("Error Data:  %s" % json.loads(resp.content)['errors'])
        quit()
    else:
        # error stop execution
        print("%s Error." % resp.status_code)
        print("Error Data:  %s" % json.loads(resp.content)['errors'])
        quit()
    return


def createloadbalancer(lb):
    ################################################
    ## create LB instance
    ################################################

    # get list of load balancers to check if instance already exists
    resp = requests.get(rias_endpoint + '/v1/load_balancers/' + version,
                        headers=headers)
    if resp.status_code == 200:
        lblist = json.loads(resp.content)["load_balancers"]
        if len(lblist) > 0:
            lblist = list(filter(lambda i: i['name'] == lb["lbInstance"], lblist))
            if len(lblist) > 0:
                print('Load Balancer named %s (%s) already exists in subnet.' % (
                    lb["lbInstance"], lblist[0]["id"]))
                return lblist[0]["id"]
    else:
        # error stop execution
        print("%s Error." % resp.status_code)
        print("Error Data:  %s" % json.loads(resp.content)['errors'])
        quit()

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
                resp = requests.get(
                    rias_endpoint + '/v1/instances/' + version + "&network_interfaces.subnet.name=" + subnet["name"],
                    headers=headers)
                if resp.status_code == 200:
                    instancelist = json.loads(resp.content)["instances"]
                for instance in subnet["instances"]:
                    if "in_lb_pool" in instance:
                        # Check if this instance is marked for this LB and pool and if so append instances to member template
                        if len(instancelist) > 0:
                            for in_lb_pool in instance["in_lb_pool"]:
                                if (in_lb_pool["lb_name"] == lb["lbInstance"]) and (
                                        in_lb_pool["lb_pool"] == pool["name"]):
                                    # iterate through quantity to find each instance
                                    for count in range(1, instance["quantity"] + 1):
                                        name = (instance["name"] % count)
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
            resp = requests.get(rias_endpoint + '/v1/subnets/' + version, headers=headers)
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
             "pools": poolTemplate
             }

    resp = requests.post(rias_endpoint + '/v1/load_balancers' + version, json=parms, headers=headers)

    if resp.status_code == 201:
        load_balancer = resp.json()
        print("Created %s (%s) load balancer successfully." % (lb["lbInstance"], load_balancer["id"]))
        return (load_balancer["id"])
    elif resp.status_code == 400:
        print("Invalid instance template provided.")
        print("template=%s" % parms)
        print("Error Data:  %s" % json.loads(resp.content)['errors'])
        quit()
    else:
        # error stop execution
        print("%s Error." % resp.status_code)
        print("Error Data:  %s" % json.loads(resp.content)['errors'])
        quit()
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

    resp = requests.get(rias_endpoint + '/v1/images/' + version, headers=headers)
    if resp.status_code == 200:
        imagelist = json.loads(resp.content)["images"]
        image_id = list(filter(lambda i: i['name'] == image_name, imagelist))
        if len(image_id) > 0:
            return image_id[0]["id"]
        else:
            return 0


def getsshkeyid(sshkey_name):
    ################################################
    ## Return the sshkey_id of an sshkey name
    ################################################

    resp = requests.get(rias_endpoint + '/v1/keys/' + version, headers=headers)
    if resp.status_code == 200:
        keylist = json.loads(resp.content)["keys"]
        sshkey_id = list(filter(lambda k: k['name'] == sshkey_name, keylist))
        if len(sshkey_id) > 0:
            return sshkey_id[0]["id"]
        else:
            return 0


def createsshkey(sshkey):
    ################################################
    ## Create new ssshkey
    ################################################

    # Check if key already exists
    if getsshkeyid(sshkey["sshkey"]) == 0:
        # create a new key

        parms = {"name": sshkey["sshkey"],
                 "public_key": sshkey["public_key"],
                 "type": "rsa"
                 }

        resp = requests.post(rias_endpoint + '/v1/keys' + version, json=parms, headers=headers)

        if resp.status_code == 201:
            print("SSH Key named %s created." % (sshkey["sshkey"]))
            return
        elif resp.status_code == 400:
            print("Invalid sshkey template provided.")
            print("template=%s" % parms)
            print("Error Data:  %s" % json.loads(resp.content)['errors'])
            quit()
        else:
            # error stop execution
            print("%s Error creating sshkey %s." % (resp.status_code, sshkey["sshkey"]))
            print("template=%s" % parms)
            print("Error Data:  %s" % json.loads(resp.content)['errors'])
            quit()

    else:
        print("SSH Key %s already exists.  Continueing" % sshkey["sshkey"])
        return



#####################################
# Set Global Variables
#####################################


# Create iam_token file by running gettoken.sh
iam_file = open("iam_token", 'r')
iam_token = iam_file.read()
iam_token = iam_token[:-1]
rias_endpoint = "https://us-south.iaas.cloud.ibm.com"
version = "?version=2019-06-04"
headers = {"Authorization": iam_token}

#####################################
# Read desired topology YAML file
#####################################

parser = argparse.ArgumentParser(description="Destroy VPC topology.")
parser.add_argument("-y", "--yaml", help="YAML based topology file to destroy")
args = parser.parse_args()
if args.yaml is None:
    filename = "topology.yaml"
else:
    filename = args.yaml

with open(filename, 'r') as stream:
    topology = yaml.load(stream, Loader=yaml.FullLoader)[0]

# Get the preferred VPC generation
if 'generation' in topology.keys():
    generation = topology["generation"]
else:
    generation = 1;

version = version + '&generation=' + str(generation)

# Determine if region identified is available and get endpoint
region = getregionavailability(topology["region"])

print("Provisioning VPC using generation %d" % generation)

if region["status"] == "available":
    # defect in the current NG api is preventing us from changing the endpoint
    if generation == 1: rias_endpoint = region["endpoint"]
    main(region["name"])
else:
    print("Region %s is not currently available." % region["name"])
    quit()
