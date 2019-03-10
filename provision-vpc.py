#!/usr/bin/env python3
## Provision-vpc - A script to provision vpc, network, and compute resources based on a templated topology yaml file.
## Author: Jon Hall
##

import requests, json, time, sys, yaml
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText


def main():
    # Determine if region identified is available
    region = getregionavailability(topology["region"])

    # Get Zones for specified region
    zones = getzones(topology['region'])

    # Create VPC
    vpc_id = createvpc()

    #######################################################################
    # Iterate through subnets in each zone and create subnets & instances
    #######################################################################

    for zone in topology["zones"]:
        for subnet in zone["subnets"]:
            ## Provision new Subnet
            subnet_id = createsubnet(vpc_id, zone["name"], subnet)
            # Build instances for this subnet (if defined in topology)
            if "instances" in subnet:
                for instance in subnet["instances"]:
                    template = getinstancetemplate(topology["instanceTemplates"], instance["template"])
                    image_id = template["image_id"]
                    profile_name = template["profile_name"]
                    sshkey_id = template["sshkey_id"]
                    user_data = encodecloudinit(template["cloud-init-file"])
                    for q in range(1, instance["quantity"] + 1):
                        instance_name = (instance["instance"] % q)
                        instance_id = createinstance(zone["name"], instance_name, vpc_id, image_id, profile_name,
                                                     sshkey_id,
                                                     subnet_id,
                                                     user_data)
                        # IF floating_ip = True assign
                        if 'floating_ip' in instance:
                            if instance['floating_ip']:
                                floating_ip_id, floating_ip_address = assignfloatingip(instance_id)

    #####################################
    # Write topology JSON state data
    #####################################
    with open('topology.json', 'w') as outfile:
        json.dump(topology, outfile)

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
            print("Desired region is available.")
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


def createpublicgateway(gateway_name, zone_name, vpc_id):
    #################################
    # Create a public gateway
    #################################

    parms = {"name": gateway_name,
             "zone": {"name": zone_name},
             "vpc": {"id": vpc_id}
             }
    resp = requests.post(rias_endpoint + '/v1/public_gateways' + version, json=parms, headers=headers)

    if resp.status_code == 201:
        gateway = resp.json()
        print("Public Gateway %s named %s was created successfully." % (gateway["id"], gateway_name))
        return (gateway)
    elif resp.status_code == 400:
        print("Invalid public gateway template provided.")
        print("template=%s" % parms)
        print("Error Data:  %s" % json.loads(resp.content)['errors'])
        quit()
    else:
        # error stop execution
        print("%s Error creating public gateway." % (resp.status_code, zone["name"]))
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
            print("The VPC named %s already exists in region. (id=%s) Continuing." % (vpc[0]["name"], vpc[0]['id']))
            default_network_acl_id = getnetworkaclid(topology["default_network_acl"])
            vpc = vpc[0]
            return (vpc['id'])
        else:
            # VPC does not exist so proceed with creating it.
            # Determine if network_acl name already exists and retreive id for use.
            resp = requests.get(rias_endpoint + '/v1/network_acls/' + version, headers=headers)
            if resp.status_code == 200:
                acls = json.loads(resp.content)["network_acls"]
                default_network_acl = \
                    list(filter(lambda acl: acl['name'] == topology["default_network_acl"]["name"], acls))

                if len(default_network_acl) > 0:
                    print("%s network_acl already exists.   Using network_acl_id %s as default." % (
                        default_network_acl[0]["name"], default_network_acl[0]['id']))
                    default_network_acl_id = default_network_acl[0]['id']

                    # create parameters for VPC creation
                    parms = {"name": topology["vpc"],
                             "classic_access": topology["classic_access"],
                             "default_network_acl": {"id": default_network_acl_id}
                             }

                    resp = requests.post(rias_endpoint + '/v1/vpcs' + version, json=parms, headers=headers)

                    if resp.status_code == 201:
                        vpc = resp.json()
                        print("Created VPC_ID %s named %s." % (vpc['id'], vpc['name']))
                        return (vpc["id"])
                    elif resp.status_code == 400:
                        print("Invalid VPC template provided.")
                        print("template=%s" % parms)
                        print("Error Data:  %s" % json.loads(resp.content)['errors'])
                        quit()
                    else:
                        # error stop execution
                        print("%s Error." % resp.status_code)
                        print("Error Data:  %s" % json.loads(resp.content)['errors'])
                        quit()
                else:
                    # *** need to create default ACL if it doesn't exist already per rules in topology file.
                    print("%s network_acls does not exists.  Please manually implement and re-run." % (
                        topology["default_network_acl"]["name"]))
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


def createsubnet(vpc_id, zone_name, subnet):
    ################################################
    ## Create new subnet is zone
    ################################################

    # get list of subnets in region to check if subnet already exists
    resp = requests.get(rias_endpoint + '/v1/subnets/' + version, headers=headers)
    if resp.status_code == 200:
        subnetlist = json.loads(resp.content)["subnets"]
        subnet_id = list(filter(lambda s: s['name'] == subnet["subnet"], subnetlist))[0]['id']
        if subnet_id != 0:
            print("Subnet named %s already exists in zone. (id=%s) Continuing." % (subnet["subnet"], subnet_id))
            return subnet_id

    network_acl_id = getnetworkaclid(subnet['network_acl'])
    parms = {"name": subnet["subnet"],
             "ipv4_cidr_block": subnet['ipv4_cidr_block'],
             "network_acl": {"id": network_acl_id},
             "zone": {"name": zone_name},
             "vpc": {"id": vpc_id}
             }
    resp = requests.post(rias_endpoint + '/v1/subnets' + version, json=parms, headers=headers)

    if resp.status_code == 201:
        newsubnet = resp.json()
        print("Subnet %s named %s was created successfully in zone %s." % (
            newsubnet["id"], subnet["subnet"], zone["name"]))
        if 'publicGateway' in subnet:
            if subnet["publicGateway"]:
                # If specified create gateway and attach.
                gateway_name = subnet["subnet"] + "-gw"
                gateway = createpublicgateway(gateway_name, zone["name"], vpc["id"])
                attach = attachpublicgateway(gateway['id'], resp.json()["id"])
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


def createinstance(zone_name, instance_name, vpc_id, image_id, profile_name, sshkey_id, subnet_id, user_data):
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
                print('Instance named %s already exists in subnet. (id=%s) Continuing.' % (
                instance_name, instancelist[0]["id"]))
                return instancelist[0]["id"]
    else:
        # error stop execution
        print("%s Error." % resp.status_code)
        print("Error Data:  %s" % json.loads(resp.content)['errors'])
        quit()

    parms = {"zone": {"name": zone_name},
             "name": instance_name,
             "vpc": {"id": vpc_id},
             "image": {"id": image_id},
             "user_data": user_data,
             "profile": {"name": profile_name},
             "keys": [{"id": sshkey_id}],
             "primary_network_interface": {
                 "port_speed": 1000,
                 "name": "eth0",
                 "subnet": {"id": subnet_id}},
             "network_interfaces": [],
             "volume_attachments": [],
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
        print("%s Error." % resp.status_code)
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
                print("Waiting for instance creation to complete.   Sleeping for 10 seconds...")
                time.sleep(10)
        else:
            print("Waiting for instance creation to complete.   Sleeping for 10 seconds...")
            time.sleep(10)

    # Check if floating IP already assigned
    resp = requests.get(
        rias_endpoint + "/v1/instances/" + instance_id + "/network_interfaces/" + network_interface + "/floating_ips" + version,
        headers=headers)
    if resp.status_code == 200:
        floating_ip = json.loads(resp.content)
        if "floating_ips" in floating_ip:
            floating_ip = floating_ip["floating_ips"]
            if len(floating_ip) > 0:
                print("Floating ip %s already assigned to %s. (id=%s) Continuing." % (
                floating_ip[0]["address"], instance_status["name"], floating_ip[0]['id']))
                return floating_ip[0]['id'], floating_ip[0]['address']

    #  Nome assigned.  Request one.
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
    return str(combined_message).encode()


def getinstancetemplate(templates, search):
    ################################################
    ## Find instance template in list
    ################################################

    template = [d for d in templates if d['template'] == search]
    return template[0]


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

with open("topology.yaml", 'r') as stream:
    topology = yaml.load(stream)[0]

main()
