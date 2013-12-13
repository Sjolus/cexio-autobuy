#!/usr/bin/env python

import re
import sys
import bs4
import time
import json
import hmac
import pprint
import urllib
import urllib2
import hashlib
import logging
import datetime
import argparse
import traceback

from decimal import *

logging.basicConfig(format='%(asctime)-18s - [%(levelname)-10s] - %(message)s', level=logging.DEBUG, filename="/home/sjolus/cexio/log/cexio.log")

getcontext().prec = 12
getcontext().rounding = ROUND_DOWN

def apicall(call, args, extravalues=None):
	"api - Make some awesome API calls"
	
	logging.debug("API call function initiated. Setting some variables!")
	global nonce
	nonce = int(nonce) + 1
	
	global callsmade
	callsmade += 1
	
	global callsleft 
	callsleft -= 1
	
	if callsleft < 10:
		logging.critical("Oh dear god I have less than 10 calls left. Panicking out")
		sys.exit(2)
	
	username = args.user
	apikey = args.apikey
	apisecret = args.apisecret
	
	url = "https://cex.io/api/"
	logging.debug("Setting URL to '%s' and got call '%s'" % (url, call))
	
	fullurl = url + call
	logging.debug("This makes '%s' the full URL!" % (fullurl))
	
	logging.debug("Now calculating that awesome HMAC signature!")
	message = str(nonce) + str(username) + str(apikey)
	datasignature = hmac.new(apisecret, message, hashlib.sha256).hexdigest().upper()
	logging.debug("Look, I made a signature: %s" % (datasignature))
	
	try:
		logging.debug("Setting some headers!")
		headers = { 'User-Agent' : 'Mozilla/5.0' }
		
		logging.debug("Setting some values")
		values = {
			'key' : apikey,
			'signature' : datasignature,
			'nonce' : nonce
		}
		
		if extravalues is not None:
			logging.debug("Setting some EXTRA values")
			for key, value in extravalues.iteritems():
				values[key] = value
		else:
			logging.debug("No extra values :(")
		data = urllib.urlencode(values)
		
		logging.debug("Performing request: fullurl: %s - data: %s - headers: %s" % (fullurl, data, headers))
		logging.debug("Performing API call #%s" % (callsmade))
		req = urllib2.Request(fullurl, data, headers)
		result = urllib2.urlopen(req).read()
		logging.debug("Request #%s (%s) performed. I have %s calls left this 10 minute period. Returning response.)" % (callsmade, fullurl, callsleft))
		logging.debug("Returning response: %s" % (result))
		return result
		
	except urllib2.HTTPError, e:
		if e.code in (502, 520, 521, 522, 524, 503):
			logging.error("I got a HTTP Error I can handle (%s - %s). Likely an outage or something." % (e.code, e))
			logging.debug("e: %s" % (e))
			logging.debug("e.code: %s" % (e.code))
			return False
		elif "IncompleteRead" in str(e):
			logging.error("I got an incomplete read! Sad stuff. Breaking off.")
			return False
		else:
			logging.critical("Unhandled exception! Crashing sys.exit error code 2 omg!")
			logging.debug("e: %s" % (e))
			logging.debug("type(e): %s" % (type(e)))
			logging.debug(traceback.print_exc())
			logging.debug(vars())
			logging.debug(e, e.fp.read())
			print e
			sys.exit(2)
	
	except urllib2.URLError, e:
		print("type: %s" % (type(e)))
		print("e: %s" % e)
		logging.error("Durr URLerror. E in stderr")
		return False

def balance(args):
	logging.debug("Balance function initiated")
	
	logging.debug("performing API call for account balances")
	response = apicall("balance/", args)
	if response is not False:
		logging.debug("API call complete, putting stuff in dicts and variables and whatnot")
		try:
			response = json.loads(response)
		
			stuff = json.loads(json.dumps(response, sort_keys=True))
		
			balancedict = 	{
								"BTC": float(stuff["BTC"]["available"]),
								"NMC": float(stuff["NMC"]["available"]),
								"IXC": float(stuff["IXC"]["available"]),
								"DVC": float(stuff["DVC"]["available"]),
								"GHS": float(stuff["GHS"]["available"])
							}
		except KeyError, e:
			logging.error("I got a bad response. Printing to console and returning False...")
			print(response)
			print(stuff)
			print("e: %s" % (e))
			return False
		logging.debug("Balance function completed, returning balance dict: %s" % (balancedict))
		return balancedict
		
        
	else:
		logging.critical("I did not receive a proper response.")
		return False
	
def autobuy(currency, balance, args):
	pp = pprint.PrettyPrinter(indent=4)
	logging.debug("Autobuy function initiated")
	
	logging.debug("Checking for pre-existing orders")
	response = apicall("open_orders/GHS/%s" % (currency), args)
	if len(response) > 0 and response != "[]":
		print len(response)
		logging.warning("I found a pre-existing order. That usually means something is broken. I'll cancel that/those order/orders.")
		for order in response:
			print order
		print response
	else:
		logging.debug("No pre-existing orders found")
	
	logging.debug("Performing API call for order book for currency %s" % (currency))
	response = apicall("order_book/GHS/%s" % (currency), args)
	
	logging.debug("Putting response into dict")
	stuff = dict(json.loads(response))
	
	logging.debug("Performing some calculations to find the cheapest sell that will match our account balance")
	for order in stuff["asks"]:
		price  = Decimal(order[0]).quantize(Decimal('1.00000000'))
		amount = Decimal(order[1]).quantize(Decimal('1.00000000'))
		ghs = Decimal(balance/price).quantize(Decimal('1.00000000'))
		if amount > ghs:
			logging.debug("%s is more than %s / %s (%s), this order is the first to match. Breaking out of loop-prison!" % (amount, balance, price, ghs))
			break
		else:
			logging.debug("This order doesn't cover our needs... Moving on up the price-ladder!")
	
	logging.debug("Placing an order to match, offering %s %s for %s GHS" % (balance / price, currency, ghs))
	response = placeorder(currency, price, ghs, args)
	stuff = dict(json.loads(response))
	logging.debug("Response: %s - %s " % (type(response), response))
	if "error" not in stuff:
		id = stuff["id"]
		pending = stuff["pending"]
		if pending == "0.00000000":
			logging.info("Order %s was instantly filled. Neat-o!" % (id))
			curdate = str(datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
			
			print("%s - ID: %s - Successfully purchased %s GHS for %s %s each. Total: %s %s" % (curdate, id, ghs, price, currency, price*ghs, currency))
		else:
			logging.warning("Order %s wasn't instantly filled. Removing." % (id))
			removeorder(id, args)
			logging.info("Order %s was placed, but not filled. It has been removed")
			
	logging.debug("Autobuy function completed.")
	return True
	
def placeorder(currency, price, ghs, args):
	logging.debug("Initiating order placement")
	logging.debug("Setting up orderdict()")
	orderdict = {
					"type"  : "buy",
					"amount": ghs,
					"price" : price
				}
	logging.debug(orderdict)
	logging.info("Placing order for %s GHS for a total value of %s %s" % (ghs.quantize(Decimal('1.00000000')), Decimal(price*ghs).quantize(Decimal('1.00000000')), currency))
	response = apicall("place_order/GHS/%s" % currency, args, orderdict)
	if "error" in response:
		logging.info("There was an error, here it is: %s (I'm also backing off for 60 extra seconds to avoid spam)" % (response))
		logging.debug(response)
		time.sleep(60)
		return response
	else: 
		return response
	
def removeorder(id, args):
	logging.debug("Initiating order removal")
	removaldict = 	{
						"id" : id
					}
	logging.info("Removing order %s" % id)
	response = apicall("cancel_order/", args, removaldict)
	logging.debug("Response: %s" % response)
	if "true" in response:
		logging.info("Order successfully canceled.")
		return response
	else:
		logging.critical("Dear god, I couldn't cancel an order. That's unhandled. Dying.")
		sys.exit(2)
		return False
	
	
def main():
	logging.warning("Starting up")
	global nonce
	nonce = str(time.time()).split('.')[0]
	
	interval = 2
	
	global maximumqueries
	maximumqueries = 30
	
	global callsmade
	callsmade = int(0)
	
	global callsleft
	callsleft = int(maximumqueries)
	
	logging.debug("Logger setup complete")
	logging.debug("Setting up argparse")
	
	parser = argparse.ArgumentParser(description='Perform various APIcalls towards cex.io')
	parser.add_argument('--action',   type=str, dest='action',    help='The action to perform', choices=['buyghs'], required=True)
	parser.add_argument('--username', type=str, dest='user',      help='Username on cex.io')
	parser.add_argument('--apikey',   type=str, dest='apikey',    help='API Key on cex.io')
	parser.add_argument('--secret',   type=str, dest='apisecret', help='cex.io API secret')
	
	logging.debug("argparse setup, parsing-time!")
	args = parser.parse_args()
	
	if args.action == "buyghs":
		logging.info("Entering infinite purchase loop. Watch for incoming BTC, buy GHS for them!")
		logging.info("Running with username %s and apikey %s" % (args.user, args.apikey))
		while True:
			try:
				logging.debug("Beginning of loop")
				
				logging.debug("Setting up some variables")
				tres = '0.00000100'
				tradelist = ("BTC", "NMC")
				balances = balance(args)
				
				if balances is False:
					logging.warning("I received an unexpected response from the API. I'm sleeping for 60 seconds, then trying again")
					time.sleep(60)
					continue
				treshold = Decimal(tres).quantize(Decimal('1.00000000'))
				
				for currency in tradelist:
					curbal = Decimal(balances[currency]).quantize(Decimal('1.00000000'))
					logging.debug("%s check! Current value: %s" % (currency, curbal))
					if curbal > treshold:
						logging.info("%s balance above %s %s (it's %s %s), initiating buy order function!" % (currency, treshold, currency, curbal, currency))
						autobuy(currency, curbal, args)
					else:
						logging.debug("%s balance below %s %s (it's %s %s), not enough to purchase for!" % (currency, treshold, currency, curbal, currency))
						
				oldcallsleft = callsleft
				if callsleft <= maximumqueries-interval:
					callsleft += interval
				elif callsleft > maximumqueries-interval and callsleft < maximumqueries:
					callsleft = maximumqueries
				else:
					logging.critical("Shit's broken... I apparently have %s calls left and that's severely unhandled" % (callsleft))
				logging.debug("End of loop... sleeping %s and raising apicallsleft from %s to %s." % (interval, oldcallsleft, callsleft))
				time.sleep(interval)
				
			except KeyboardInterrupt:
				print("\nCtrl+C detected. Shutting down")
				logging.error("Ctrl+C detected. Shutting down")
				break
			except Exception as e:
				traceback.print_exc()
				print(str(e.__doc__))
				print(str(e.message))
				print(e)
				print(type(e))
				print(vars())
					
				logging.critical("Exception caugt, exiting %s" % (e))
				sys.exit(2)
	print("Command completed. Shutting down")
	logging.warning("Command completed. Shutting down")
	sys.exit(0)

if __name__ == '__main__':
    main()
