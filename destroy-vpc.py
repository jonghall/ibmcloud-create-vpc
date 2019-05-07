#!/usr/bin/env python3
## destroy vpc - A script to de-provision vpc, network, and compute resources based on a templated topology yaml file.
## Author: Jon Hall
##

import requests, json, time, yaml, argparse
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText


def main(region):
    #######################################################################
    # Work backwards to remove objects
    #######################################################################

    # Get VPC_ID
    vpc_name = topology["vpc"]
    vpc_id = getvpcid(vpc_name)

    if vpc_id is not None:

        #######################################################################
        # Delete load balancers
        #######################################################################
        print("- Deleting Load Balancers -")
        if "load_balancers" in topology:
            for lb in topology["load_balancers"]:
                deleteloadbalancer(lb)

        #######################################################################
        # Detach Floating IPs & gateways & delete instance
        #######################################################################

        for zone in topology["zones"]:

            print("-- zone %s --" % zone["name"])
            for subnet in zone["subnets"]:
                print("--- subnet %s ---" % subnet["name"])
                if subnet["publicGateway"]:
                    detachpublicgateway(subnet["name"])

                if "vpn" in subnet:
                    ## delete VPN
                    for vpn in subnet["vpn"]:
                        # delete each vpn instance
                        vpn_id = getvpnid(vpn["name"])
                        deletevpn(vpn_id, vpn["name"])

                for instance in subnet["instances"]:
                    for q in range(1, instance["quantity"] + 1):
                        instance_name = (instance["name"] % q)
                        print("---- instance %s ----" % instance_name)

                        # check if floating ip's exist
                        id = detachfloatingip(instance_name, subnet["name"])
                        if id is not None:
                            # if floating ip is detached release it
                            releasefloatingip(id)

                        # now that ip is detached and deleted or didn't exist delete instance
                        deleteinstance(instance_name, subnet["name"])

                # now that instances are deleted delete subnet
                deletesubnet(subnet["name"])
            deletepublicgateway(zone["name"], vpc_name, vpc_id)

            if "address_prefix_cidr" in zone:
                name = zone["name"] + "-address-prefix"
                deleteaddressprefix(vpc_id, name, zone["name"])


        #######################################################################
        # Delete Security Groups
        #######################################################################
        print("- Deleting Security Groups -")
        for security_group in topology["security_groups"]:
            deletesecuritygroup(security_group["security_group"], vpc_id)

        #######################################################################
        # Delete VPC
        #######################################################################
        print("- Deleting VPC -")
        deletevpc(vpc_id, vpc_name, topology["region"])

    #######################################################################
    # Delete Network ACLS
    #######################################################################
    print("- Deleting Network Acls -")
    for network_acl in topology["network_acls"]:
        deletenetworkacls(network_acl["network_acl"])

    #######################################################################
    # Delete Keys
    #######################################################################
    print("- Deleting sshkeys -")
    for sshkey in topology["sshkeys"]:
        deletesshkey(sshkey["sshkey"])
    return


def getvpnid(vpn_name):
    ################################################
    ## LLookup VPN by name
    ################################################

    resp = requests.get(rias_endpoint + '/v1/vpn_gateways' + version, headers=headers)
    if resp.status_code == 200:
        vpn_gateways = json.loads(resp.content)["vpn_gateways"]
        vpn_gateway = \
            list(filter(lambda vpn_gateway: vpn_gateway["name"] == vpn_name, vpn_gateways))

        if len(vpn_gateway) > 0:
            vpn_gateway_id = vpn_gateway[0]['id']
        else:
            vpn_gateway_id = None
    else:
        vpn_gateway_id = None

    return vpn_gateway_id

def getregionavailability(region):
    #############################
    # Get Region Availability
    #############################

    resp = requests.get(rias_endpoint + '/v1/regions/' + region + version, headers=headers)

    if resp.status_code == 200:
        region = json.loads(resp.content)

        if topology["region"] == region["name"] and region["status"] == "available":
            print("Connected to Region %s." % region["name"])
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
            return
    else:
        return

    return network_acl_id


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
            return
    else:
        return

    return security_group_id


def deletenetworkacls(network_acl_name):
    ################################################
    ## Delete network acl
    ################################################

    network_acl_id = getnetworkaclid(network_acl_name)

    if network_acl_id is not None:
        # Delete network ACL

        resp = requests.delete(rias_endpoint + '/v1/network_acls/' + network_acl_id + version, headers=headers)

        if resp.status_code == 204:
            print("Network ACL %s deleted successfully." % (network_acl_name))
        elif resp.status_code == 409:
            print("Network ACL %s cannot be deleted. It is the default security group for this VPC." % network_acl_name)
            #             # continue default acl will be deleted when VPC is deleted
        elif resp.status_code == 404:
            print("A network ACL with id %s  could not be found." % network_acl_id)
            print("Error Data:  %s" % json.loads(resp.content)['errors'])
            quit()
        else:
            print("%s Error deleting network acl." % (resp.status_code))
            print("Error Data:  %s" % json.loads(resp.content)['errors'])
            quit()
    else:
        # Security group already exists.  do no recreate
        print("Network ACL %s does not exists" % (network_acl_name))
    return


def deletesecuritygroup(security_group, vpc_id):
    ################################################
    ## create security group
    ################################################

    security_group_id = getsecuritygroupid(security_group, vpc_id)

    if security_group_id is not None:
        # Delete Security Group

        resp = requests.delete(rias_endpoint + '/v1/security_groups/' + security_group_id + version, headers=headers)

        if resp.status_code == 204:
            print("Security Group %s deleted successfully." % (security_group))
        elif resp.status_code == 400:
            print(
                "Security group %s cannot be deleted. It is the default security group for a virtual private cloud." % security_group)
            #             # continue default security group will be deleted when VPC is deleted
        elif resp.status_code == 404:
            print("A security group with id %s  could not be found." % security_group_id)
            print("Error Data:  %s" % json.loads(resp.content)['errors'])
            quit()
        else:
            print("%s Error deleting security group." % (resp.status_code))
            print("Error Data:  %s" % json.loads(resp.content)['errors'])
            quit()
    else:
        # Security group already exists.  do no recreate
        print("Security Group %s does not exists" % (security_group))
    return


def getpublicgatewayid(name, vpc_id):
    # A gateway is needed check if Public Gateway already exists in zone, if not create.
    resp = requests.get(rias_endpoint + '/v1/public_gateways' + version, headers=headers)
    if resp.status_code == 200:
        public_gateways = json.loads(resp.content)["public_gateways"]
        # Determine if gateway exists and use it.  First get Gateways for this VPC
        public_gateway = list(filter(lambda gw: gw['vpc']['id'] == vpc_id, public_gateways))
        # Determine if gateway exists in this vpc for this zone
        public_gateway = list(filter(lambda gw: gw['name'] == name, public_gateway))

        if len(public_gateway) > 0:
            # gateway already exists, get it's ID and attach to subnet.
            return public_gateway[0]["id"]
    return


def deletepublicgateway(zone_name, vpc_name, vpc_id):
    #################################
    # CDelete a public gateway
    #################################

    gateway_name = vpc_name + "-" + zone_name + "-gw"

    public_gateway_id = getpublicgatewayid(gateway_name, vpc_id)

    if public_gateway_id is not None:
        # gateway already exists, delete it
        resp = requests.delete(rias_endpoint + '/v1/public_gateways/' + public_gateway_id + version,
                               headers=headers)

        if resp.status_code == 204:
            print("Public Gateway %s deleted successfully." % (gateway_name))
        elif resp.status_code == 404:
            print("Public Gateway not found.")
            print("Error Data:  %s" % json.loads(resp.content)['errors'])
            quit()
        elif resp.status_code == 409:
            print("Public Gateway in use and can not be deleted.")
            print("Error Data:  %s" % json.loads(resp.content)['errors'])
            quit()
        else:
            # error stop execution
            print("%s Error deleting public gateway." % (resp.status_code, zone_name))
            print("Error Data:  %s" % json.loads(resp.content)['errors'])
            quit()
    else:
        # No gateway continue
        print("No gateway for zone %s found." % zone_name)
    return


def detachpublicgateway(subnet_name):
    #################################
    # Detach a public gateway
    #################################

    subnet_id = getsubnetid(subnet_name)

    if subnet_id != None:
        resp = requests.get(rias_endpoint + '/v1/subnets/' + subnet_id + version, headers=headers)
        if resp.status_code == 200:
            subnet = json.loads(resp.content)
            if "public_gateway" in subnet:
                resp = requests.delete(rias_endpoint + '/v1/subnets/' + subnet_id + '/public_gateway' + version,
                                       headers=headers)
                if resp.status_code == 204:
                    print("Public gateway detached successfully from subnet %s." % subnet_name)
                    return
                elif resp.status_code == 404:
                    print("A subnet with the specified identifier could not be found.")
                    print("Response:  %s" % json.loads(resp.content))
                    quit()
                else:
                    # error stop execution
                    print("%s Error detaching pubic gateway from subnet %s." % (resp.status_code, subnet_name))
                    print("Error Data:  %s" % json.loads(resp.content)['errors'])
                    quit()
            else:
                print("No public Gateway exists for subnet %s" % subnet_name)
        else:
            print("%s Error getting public gateway from subnet %s." % (resp.status_code, subnet_name))
            print("template=%s" % parms)
            print("Error Data:  %s" % json.loads(resp.content)['errors'])
            quit()
    return


def getvpcid(vpc_name):
    resp = requests.get(rias_endpoint + '/v1/vpcs/' + version, headers=headers)
    if resp.status_code == 200:
        vpcs = json.loads(resp.content)["vpcs"]
        # Determine if network_acl name already exists and retreive id.
        vpc = list(filter(lambda vpc: vpc['name'] == vpc_name, vpcs))
        if len(vpc) > 0:
            return (vpc[0]['id'])
        else:
            return


def deletevpc(vpc_id, vpc_name, region):
    ##################################
    # Delete VPC in desired region
    ##################################

    resp = requests.delete(rias_endpoint + '/v1/vpcs/' + vpc_id + version, headers=headers)

    if resp.status_code == 204:
        print("Deleted VPC named %s (%s) in region %s." % (vpc_name, vpc_id, region))
    elif resp.status_code == 400:
        print("VPC named %s (%s) in region %s could not be deleted." % (vpc_name, vpc_id, region))
        print("Error Data:  %s" % json.loads(resp.content)['errors'])
        quit()
    elif resp.status_code == 404:
        print("VPC ID %s could not be found." % (vpc_id))
        print("Error Data:  %s" % json.loads(resp.content)['errors'])
        quit()
    elif resp.status_code == 409:
        print("VPC named %s (%s) in region %s is in use and can not be deleted." % (vpc_name, vpc_id, region))
        print("Error Data:  %s" % json.loads(resp.content)['errors'])
        quit()
    else:
        print("%s Error." % resp.status_code)
        print("Error Data:  %s" % json.loads(resp.content)['errors'])
        quit()
    return


def getaddressprefixid(vpc_id, name):
    # get list of prefixes in VPC to check if prefix already exists
    resp = requests.get(rias_endpoint + '/v1/vpcs/' + vpc_id + '/address_prefixes' + version, headers=headers)
    if resp.status_code == 200:
        prefixlist = json.loads(resp.content)["address_prefixes"]
        prefix_id = list(filter(lambda p: p['name'] == name, prefixlist))
        if len(prefix_id) > 0:
            if prefix_id[0]["id"] != 0:
                return prefix_id[0]["id"]
    return


def deleteaddressprefix(vpc_id, name, zone):
    ################################################
    ## Deletes Prefix in VPC
    ################################################

    addressprefix_id = getaddressprefixid(vpc_id, name)

    if addressprefix_id is not None:
        resp = requests.delete(rias_endpoint + '/v1/vpcs/' + vpc_id + '/address_prefixes/' + addressprefix_id + version,
                               headers=headers)

        if resp.status_code == 204:
            print("vpc-address-prefix %s deleted successfully in zone %s." % (
                name, zone))
            return
        elif resp.status_code == 404:
            print("Prefix id %s could not be found." % addressprefix_id)
            quit()
        elif resp.status_code == 409:
            print("Prefix %s is in use and can not be deleted." % name)
            quit()
        else:
            print("%s Error deleting vpc-address-prefix in %s zone." % (resp.status_code, zone))
            print("Error Data:  %s" % json.loads(resp.content)['errors'])
            quit()
    print("Address prefix named %s not found." % name)
    return


def getsubnetid(subnet_name):
    ################################################
    ## get subnet id from name
    ################################################

    # get list of subnets in region to find id
    resp = requests.get(rias_endpoint + '/v1/subnets/' + version, headers=headers)
    if resp.status_code == 200:
        subnetlist = json.loads(resp.content)["subnets"]
        subnet_id = list(filter(lambda s: s['name'] == subnet_name, subnetlist))
        if len(subnet_id) > 0:
            return subnet_id[0]["id"]
        else:
            return


def deletesubnet(subnet_name):
    ################################################
    ## delete subnet
    ################################################

    subnet_id = getsubnetid(subnet_name)

    if subnet_id != None:
        resp = requests.delete(rias_endpoint + '/v1/subnets/' + subnet_id + version, headers=headers)

        if resp.status_code == 204:
            print("Subnet named %s deleted." % (subnet_name))
            while True:
                print("Waiting for deletion of subnet %s to complete.  Sleeping 10 seconds." % subnet_name)
                time.sleep(10)
                if getsubnetid(subnet_name) is None:
                    break
        elif resp.status_code == 409:
            print("Subnet %s is in use and can not be deleted." % subnet_name)
            print("Error Data:  %s" % json.loads(resp.content)['errors'])
            quit()
        else:
            # error stop execution
            print("%s Error deleting subnet %s" % (resp.status_code, subnet_name))
            print("Error Data:  %s" % json.loads(resp.content)['errors'])
            quit()
    return


def detachfloatingip(instance_name, subnet_name):
    ################################################
    ## detach floating ip from interface
    ################################################

    # Get instance ID
    instance_id = getinstanceid(instance_name, subnet_name)

    if instance_id is not None:
        # get network interfaces
        resp = requests.get(
            rias_endpoint + '/v1/instances/' + instance_id + "/network_interfaces" + version,
            headers=headers)

        if resp.status_code == 200:
            network_interfaces = json.loads(resp.content)["network_interfaces"]

            if len(network_interfaces) > 0:
                for network_interface in network_interfaces:
                    # for each network interface get floating ips
                    resp = requests.get(
                        rias_endpoint + '/v1/instances/' + instance_id + "/network_interfaces/" + network_interface[
                            "id"] + "/floating_ips" + version,
                        headers=headers)

                    if resp.status_code == 200:
                        response = json.loads(resp.content)
                        if "floating_ips" in response:
                            floating_ips = response["floating_ips"]
                            for floating_ip in floating_ips:
                                # For each floating Ip detach it.
                                resp = requests.delete(
                                    rias_endpoint + '/v1/instances/' + instance_id + "/network_interfaces/" +
                                    network_interface["id"] + "/" + "floating_ips/" + floating_ip["id"] + version,
                                    headers=headers)

                                if resp.status_code == 204:
                                    print(
                                        "Floating IP %s detached from instance %s" % (floating_ip["id"], instance_name))
                                    return floating_ip["id"]
                                elif resp.status_code == 400:
                                    print("Floating IP %s could not be disassociated." % floating_ip["id"])
                                elif resp.status_code == 404:
                                    print("An instance with id %s could not be found." % instance_id)
                                else:
                                    print("Error in disassociating floating ip from instance %s" % instance_name)
                        else:
                            print("No floating IP's found for instance %s." % instance_name)
                    else:
                        print("Error retrieving floating IPs for instance %s." % instance_name)
            else:
                print("No network interfaces found for instance %s." % instance_name)
        else:
            print("Error returning network interface for instance %s." % instance_name)

    return


def releasefloatingip(id):
    ################################################
    ## rrelease floating ip
    ################################################

    while True:
        resp = requests.get(rias_endpoint + '/v1/floating_ips/' + id + version, headers=headers)
        if resp.status_code == 200:
            if "status" in json.loads(resp.content):
                status = json.loads(resp.content)["status"]
                if status == "available":
                    break
                else:
                    print("Waiting for floating ip %s to detach.  Sleeping 10 seconds." % id)
                    time.sleep(10)

    resp = requests.delete(rias_endpoint + '/v1/floating_ips/' + id + version, headers=headers)

    if resp.status_code == 204:
        print("Floating IP %s deleted." % (id))
    elif resp.status_code == 404:
        print("The specified floating IP %s could not be found." % id)
        quit()
    elif resp.status_code == 409:
        print("The floating IP %s is in use and cannot be deleted." % id)
        quit()
    else:
        # error stop execution
        print("%s Error deleting floating ip %s" % (resp.status_code, id))
        print("Error Data:  %s" % json.loads(resp.content)['errors'])
        quit()

    return


def getinstanceid(instance_name, subnet_name):
    ##############################################
    # get instance id from name
    ##############################################

    # get list of instances to check if instance already exists
    resp = requests.get(rias_endpoint + '/v1/instances/' + version + "&network_interfaces.subnet.name=" + subnet_name,
                        headers=headers)
    if resp.status_code == 200:
        instancelist = json.loads(resp.content)["instances"]
        if len(instancelist) > 0:
            instancelist = list(filter(lambda i: i['name'] == instance_name, instancelist))
            if len(instancelist) > 0:
                return instancelist[0]["id"]
    else:
        return


def deletevpn(vpn_id, vpn_name):
    ##############################################
    # delete vpn
    ##############################################

    if vpn_id != None:
        resp = requests.delete(rias_endpoint + '/v1/vpn_gateways/' + vpn_id + version, headers=headers)

        if resp.status_code == 204:
            print("vpn %s (%s) deleted successfully." % (vpn_name, vpn_id))
            while True:
                print("Waiting for deletion of instance %s to complete.  Sleeping 30 seconds." % vpn_name)
                time.sleep(30)
                if getvpnid(vpn_name) is None:
                    break

        elif resp.status_code == 404:
            print("An vpn with the specified identifier %s could not be found." % vpn_id)
            print("Error Data:  %s" % json.loads(resp.content)['errors'])
            quit()

        else:
            print("%s Error deleting instance." % resp.status_code)
            print("Error Data:  %s" % json.loads(resp.content)['errors'])
            quit()
    else:
        print("VPN %s does not currently exist." % (vpn_name))
    return

def deleteinstance(instance_name, subnet_name):
    ##############################################
    # delete instance
    ##############################################

    instance_id = getinstanceid(instance_name, subnet_name)

    if instance_id != None:
        resp = requests.delete(rias_endpoint + '/v1/instances/' + instance_id + version, headers=headers)

        if resp.status_code == 204:
            print("Instance %s (%s) deleted successfully." % (instance_name, instance_id))
            while True:
                print("Waiting for deletion of instance %s to complete.  Sleeping 30 seconds." % instance_name)
                time.sleep(30)
                if getinstanceid(instance_name, subnet_name) is None:
                    break

        elif resp.status_code == 404:
            print("An instance with the specified identifier %s could not be found." % instance_id)
            print("Error Data:  %s" % json.loads(resp.content)['errors'])
            quit()
        elif resp.status_code == 409:
            print(
                "Can not delete instance %s when it's already in deleting/pending/starting/stopping/restarting status." % instance_name)
            print("Error Data:  %s" % json.loads(resp.content)['errors'])
            quit()
        else:
            print("%s Error deleting instance." % resp.status_code)
            print("Error Data:  %s" % json.loads(resp.content)['errors'])
            quit()
    else:
        print("Instance %s does not currently exist in subnet %s." % (instance_name, subnet_name))
    return


def getloadbalancerid(lbname):
    ################################################
    ## get LB instance id
    ################################################

    # get list of load balancers and return information
    resp = requests.get(rias_endpoint + '/v1/load_balancers/' + version,
                        headers=headers)
    if resp.status_code == 200:
        lblist = json.loads(resp.content)["load_balancers"]
        if len(lblist) > 0:
            lblist = list(filter(lambda i: i['name'] == lbname, lblist))
            if len(lblist) > 0:
                if lblist[0]["operating_status"] == "online":
                    return lblist[0]["id"]
            else:
                return
    else:
        # error stop execution
        print("%s Error." % resp.status_code)
        print("Error Data:  %s" % json.loads(resp.content)['errors'])
        quit()
    return


def deleteloadbalancer(lb):
    ################################################
    ## delete LB instance
    ################################################

    lb_id = getloadbalancerid(lb["lbInstance"])

    if lb_id is not None:

        resp = requests.delete(rias_endpoint + '/v1/load_balancers/' + lb_id + version, headers=headers)

        if resp.status_code == 204:
            print("Deleted %s (%s) load balancer successfully." % (lb["lbInstance"], lb_id))
            while True:
                print("Waiting for deletion of load balancer %s to complete.  Sleeping 30 seconds." % lb["lbInstance"])
                time.sleep(30)
                if getloadbalancerid(lb["lbInstance"]) is None:
                    break
        elif resp.status_code == 404:
            print("A load balancer with that id cloud not be found.")
            print("Error Data:  %s" % json.loads(resp.content)['errors'])
            quit()
        else:
            # error stop execution
            print("%s Error." % resp.status_code)
            print("Error Data:  %s" % json.loads(resp.content)['errors'])
            quit()

    else:
        print("There are no loadbalancers named %s" % lb["lbInstance"])

    return


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
            return


def deletesshkey(sshkey_name):
    ################################################
    ## delete ssshkey
    ################################################
    sshkey_id = getsshkeyid(sshkey_name)

    if sshkey_id is not None:

        resp = requests.delete(rias_endpoint + '/v1/keys/' + sshkey_id + version, headers=headers)

        if resp.status_code == 204:
            print("SSH Key named %s deleted." % (sshkey_name))
            return
        elif resp.status_code == 400:
            print("SSH Key %s (%s) could not be deleted." % (sshkey_name, sshkey_id))
            print("Error Data:  %s" % json.loads(resp.content)['errors'])
            quit()
        elif resp.status_code == 404:
            print("SSH Key %s (%s) could not be found." % (sshkey_name, sshkey_id))
            print("Error Data:  %s" % json.loads(resp.content)['errors'])
            quit()
        else:
            print("%s Error creating sshkey %s." % (resp.status_code, sshkey["sshkey"]))
            print("Error Data:  %s" % json.loads(resp.content)['errors'])
            quit()

    else:
        print("SSH Key %s does not exist." % sshkey_name)

    return


#####################################
# Set Global Variables
#####################################


# Create iam_token file by running gettoken.sh
iam_file = open("iam_token", 'r')
iam_token = iam_file.read()
iam_token = iam_token[:-1]
rias_endpoint = "https://us-south.iaas.cloud.ibm.com"
version = "?version=2019-01-01"
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
    topology = yaml.load(stream)[0]

# Determine if region identified is available and get endpoint
region = getregionavailability(topology["region"])

if region["status"] == "available":
    rias_endpoint = region["endpoint"]
    main(region["name"])
else:
    print("Region %s is not currently available." % region["name"])
    quit()
