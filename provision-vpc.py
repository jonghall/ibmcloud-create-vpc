#!/usr/bin/env python3

import requests, json, yaml, time

def getZones(region):
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

def getRegionAvailability(region):
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

def getNetworkACLId(network_acl_name):
    # Search for network acl id by name and return network_acl_id
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

def createPublicGateway(gateway_name, zone_name, vpc_id):
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

def attachPublicGateway(gateway_id, subnet_id):
    #################################
    # Attach a public gateway
    #################################

    # Check subnet status first...waiting up to 30 seconds
    count=0
    while count < 12:
        resp = requests.get(rias_endpoint + '/v1/subnets/'+subnet_id + version, headers=headers);
        subnet_status = json.loads(resp.content)["status"]
        if  subnet_status == "available":
            break
        else:
            print ("Waiting for subnet creation before attaching public gateway.   Sleeping for 5 seconds...")
            count += 1
            time.sleep(5)

    parms = {"id": gateway_id}
    resp = requests.put(rias_endpoint + '/v1/subnets/'+subnet_id+'/public_gateway' + version, json=parms,
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
        print("Request: %s" % rias_endpoint + '/v1/subnets/'+subnet_id+'/public_gateway' + version)
        print("Response:  %s" % json.loads(resp.content))
        quit()
    else:
        # error stop execution
        print("%s Error attaching pubic gateway." % (resp.status_code, zone["name"]))
        print("template=%s" % parms)
        print("Error Data:  %s" % json.loads(resp.content)['errors'])
        quit()

    return

def createVPC():

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
            print("%s VPC already exists id=%s. Continuing." % (vpc[0]["name"], vpc[0]['id']))
            default_network_acl_id = getNetworkACLId(topology["default_network_acl"])
            vpc = vpc[0]
            return (vpc)
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
                        return (vpc)
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

def createSubnet(subnet):
    # Define parameters
    network_acl_id = getNetworkACLId(subnet['network_acl'])
    parms = {"name": subnet["subnet"],
             "ipv4_cidr_block": subnet['ipv4_cidr_block'],
             "network_acl": {"id": network_acl_id},
             "zone": {"name": zone['name']},
             "vpc": {"id": vpc['id']}
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
                gateway = createPublicGateway(gateway_name, zone["name"], vpc["id"])
                attach = attachPublicGateway(gateway['id'], resp.json()["id"])
        return newsubnet
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

#####################################
# Read desired topology YAML file
#####################################

with open("topology.yaml", 'r') as stream:
    topology = yaml.load(stream)[0]

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

# Determine if region is available
region = getRegionAvailability(topology["region"])

# Get Zones for specified region
zones = getZones(topology['region'])

# Create VPC
vpc = createVPC()

# Iterate through subnets in each zone and create them
subnets = []
for zone in topology["zones"]:
    for subnet in zone["subnets"]:
       newsubnet = createSubnet(subnet)
       subnets.append(newsubnet)

print (json.dumps(subnets, indent=2))

