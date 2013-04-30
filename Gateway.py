#
#  Copyright (c) 2013 Felix Rusu (felix@lowpowerlab.com).  All rights reserved.
#
#  This code is free software; you can redistribute it and/or
#  modify it under the terms of the GNU Lesser General Public
#  License as published by the Free Software Foundation; either
#  version 2.1 of the License, or (at your option) any later version.
#
#  This code is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
#  Lesser General Public License for more details.
#
#  You should have received a copy of the GNU Lesser General Public
#  License along with this library; if not, write to the Free Software
#  Foundation, Inc., 51 Franklin St, Fifth Floor, Boston, MA  02110-1301  USA
#

#ADD TWITTER CODE FROM: https://github.com/adafruit/Tweet-a-Watt/blob/master/wattcher.py
import time, sys, serial, threading
import collections
import httplib
import string
import smtplib
import re

### GENERAL SETTINGS ###
SERIALPORT = "/dev/ttyAMA0"  # the default com/serial port the receiver is connected to
BAUDRATE = 115200            # default baud rate we talk to Moteino

### USE THESE NUMBERS TO TWEAK YOUR KAW ADC input SAMPLING ###
CURRENTNORM = 15.8           # conversion to amperes from ADC
VOLTAGENORM = 0.94           # multiply each volt sample by this

MAINSVPP = 169.7 * 2         # +-170V is what 120Vrms ends up being (= 120*2sqrt(2))
SAMPLECOUNT = 28             # expected sample count per packet
GRAPHIT = False              # default of whether to graph V/A/Watts
NUMWATTDATASAMPLES = 1800    # when graphing, how many samples to watch in the plot window (1800 ~= 30min)
DEBUG = False

# EMONCMS Settings
EMONPOST = True
EMONAPIKEY = "your EmonCMS key HERE"
EMONHOST = "localhost"
EMONHOSTPORT = 80
SUMP_LVL_LIMIT = 20
SUMP_ALERT_LASTSENT = 0 # send an email every so often when SUMP pump is dangerously full (minutes)

# Read command line arguments
if (sys.argv and len(sys.argv) > 1):
  if len(sys.argv)==2 and sys.argv[1] == "-h":
    print " -d               Set DEBUG=True"
    print " -g               Set GRAPH=True (requires these python libs: wx, numpy, matplotlib, pylab)"
    print " -s SPort         Read from serial port SPort (Default: ", SERIALPORT, ")"
    print " -b Baud          Set serial port bit rate to Baud (Default: ", BAUDRATE, ")"
    print " -emonhost HOST   Set EMONHost to HOST (Default: ", EMONHOST, ")"
    print " -emonkey  KEY    Set EMONAPIKey to KEY"
    print " -emonport PORT   Set EMONHostPort to PORT (Default: ", EMONHOSTPORT, ")"
    print " -h               Print this message"
    exit(0)
    
  for i in range(len(sys.argv)):
    if sys.argv[i] == "-d":
      DEBUG = True
    if sys.argv[i] == "-g":
      GRAPHIT = True
    if sys.argv[i] == "-s" and len(sys.argv) >= i+2:
      SERIALPORT = sys.argv[i+1]
    if sys.argv[i] == "-b" and len(sys.argv) >= i+2:
      BAUD = sys.argv[i+1]
    if sys.argv[i] == "-emonhost" and len(sys.argv) >= i+2:
      EMONHOST = sys.argv[i+1]
    if sys.argv[i] == "-emonkey" and len(sys.argv) >= i+2:
      EMONAPIKEY = sys.argv[i+1]
    if sys.argv[i] == "-emonport" and len(sys.argv) >= i+2:
      EMONHOSTPORT = sys.argv[i+1]


if GRAPHIT:
  import wx
  import numpy as np
  import matplotlib
  matplotlib.use('WXAgg') # do this before importing pylab
  from pylab import *

  # Create an animated graph
  fig = plt.figure()
  fig.canvas.set_window_title('Plots: Watts, Volts, Amps') 
  # with three subplots: line voltage/current, watts and watthr
  wattusage = fig.add_subplot(211)
  mainswatch = fig.add_subplot(212)
  
  # data that we keep track of, the average watt usage as sent in
  avgwattdata = [0] * NUMWATTDATASAMPLES # zero out all the data to start
  avgwattdataidx = 0 # which point in the array we're entering new data
  
  # The watt subplot
  watt_t = np.arange(0, len(avgwattdata), 1)
  wattusageline, = wattusage.plot(watt_t, avgwattdata)
  wattusage.set_ylabel('Watts')
  wattusage.set_ylim(0, 500)
  wattusage.grid(True)
  wattslabel = wattusage.text(.01,.92,"", transform = wattusage.transAxes)
  
  # the mains voltage and current level subplot
  mains_t = np.arange(0, SAMPLECOUNT, 1)
  voltagewatchline, = mainswatch.plot(mains_t, [0] * SAMPLECOUNT, color='blue')
  mainswatch.set_ylabel('Volts (blue)')
  mainswatch.set_xlabel('Sample #')
  mainswatch.set_ylim(-200, 200)
  mainswatch.grid(True)
  # make a second axies for amp data
  mainsampwatcher = mainswatch.twinx()
  ampwatchline, = mainsampwatcher.plot(mains_t, [0] * SAMPLECOUNT, color='green')
  mainsampwatcher.set_ylabel('Amps (green)')
  mainsampwatcher.set_ylim(-15, 15)
  # and a legend for both of them
  legend((voltagewatchline, ampwatchline), ('V', 'A'))
    

# open up the FTDI serial port to get data transmitted to Moteino
ser = serial.Serial(SERIALPORT, BAUDRATE, timeout=10)

#helper function
def isnumeric(s):
  try:
    float(s)
    return True
  except ValueError:
    return False

#read sample data into a numeric array
#KAW sample data is base32 encoded and shifted 59 positions to the right
def parse_samples(samples):
  howmany = (len(samples)/2)
  if howmany != SAMPLECOUNT:
    print "Expected ", SAMPLECOUNT, " samples, received packet with ", howmany, ", skipping..."
    return [0] * SAMPLECOUNT
  output=[0] * howmany
  for i in range(howmany):
    sample = (ord(samples[i*2])-59)*32 + ord(samples[i*2+1])-59
    output[i] = sample
  return output

graphIsOutdated = False
def powercalc(nodeID):
  global voltagedata, ampdata, avgwattdataidx, avgwattdata, avgwatts, shouldUpdateGraph, graphIsOutdated
  samplecount = len(voltagedata)
  
  # get max and min voltage and normalize the curve to '0' to make the graph 'AC coupled' / signed
  min_v = 1023 #samples are 10 bit (atmega328p ADC)
  max_v = 0
  for i in range(samplecount):
    if (min_v > voltagedata[i]):
      min_v = voltagedata[i]
    if (max_v < voltagedata[i]):
      max_v = voltagedata[i]

  # figure out the 'average' of the max and min readings
  avgv = (max_v + min_v) / 2.0
  # also calculate the peak to peak measurements
  vpp =  max_v-min_v
  if vpp==0: vpp=0.1 #avoid Div/0 exceptions in case voltage waveform drops to 0 for some reason
  
  # get max and min amps and normalize the curve to '0' to make the graph 'AC coupled' / signed
  avga = 0
  
  # attempt to clean ampdata when no load is present
  # in this case ampdata most often appears as a parasitic variation around the center value (ie 490 center, ampdata: 490, 491, 491, 490, 490, 490, ..., 491)
  counter = collections.Counter(ampdata)
  #print counter[0]
  if (len(counter)==2 and abs(counter.keys()[0]-counter.keys()[1])<=2):
    ampdata = [max(counter.keys()[0], counter.keys()[1])] * samplecount

  for i in range(samplecount):
    avga += ampdata[i]
  avga /= (len(ampdata) * 1.0)
  
  # normalize voltage data
  for i in range(samplecount):
    #remove 'dc bias', which we call the average read
    voltagedata[i] -= avgv
    # We know that the mains voltage is 120Vrms = +-170Vpp
    voltagedata[i] = ((voltagedata[i] * MAINSVPP) / vpp) * VOLTAGENORM
  
  # normalize current readings to amperes
  for i in range(samplecount):
    # VREF is the hardcoded 'DC bias' value, its about 492
    ampdata[i] -= avga
    # the CURRENTNORM is our normalizing constant that converts the ADC reading to Amperes
    ampdata[i] /= CURRENTNORM

  if DEBUG:
    print "Voltage, in volts: ", voltagedata
    print "Current, in amps:  ", ampdata

  # calculate instant. watts, by multiplying V*I for each sample point
  wattdata = [0] * samplecount
  for i in range(samplecount):
    wattdata[i] = voltagedata[i] * ampdata[i]

  avgamps = 0
  for i in range(samplecount):
    avgamps += abs(ampdata[i])
  avgamps /= (samplecount*1.0)

  avgwatts = 0
  for i in range(samplecount):         
    avgwatts += abs(wattdata[i])
  avgwatts /= (samplecount*1.0)

  if DEBUG:
    # Print out our most recent measurements
    print "  Amp draw(A)  : "+str(avgamps)
    print "  Watt draw(VA): "+str(avgwatts)

  graphIsOutdated = True

  #if EMONPOST:
  #  sendToEMONCMS(nodeID, "json={power:"+str(avgwatts)+"}");
  return avgwatts;

def sendToEMONCMS(nodeID, jsonString):
  conn = httplib.HTTPConnection(EMONHOST, EMONHOSTPORT)
  requeststr = "/emoncms/input/post?apikey=" + EMONAPIKEY + "&node=" + str(nodeID) + "&" + jsonString
  conn.request("GET", requeststr)
  if DEBUG: print "GET " + EMONHOST + requeststr
  result = conn.getresponse()
  if DEBUG: print str(result.status) + " " + str(result.reason)
  conn.close()

def updategraph(idleevent):
  global voltagedata, ampdata, avgwattdataidx, avgwattdata, graphIsOutdated
  global fig, plt, wattusageline, voltagewatchline, ampwatchline, mainsampwatcher, wattusage
  global wattslabel
  
  if GRAPHIT and graphIsOutdated:
    # Add the current watt usage to our graph history
    avgwattdata[avgwattdataidx] = avgwatts
    avgwattdataidx += 1
    
    if (avgwattdataidx >= len(avgwattdata)):
      # If we're running out of space, shift the first 10% out
      tenpercent = int(len(avgwattdata)*0.1)
      for i in range(len(avgwattdata) - tenpercent):
        avgwattdata[i] = avgwattdata[i+tenpercent]
      for i in range(len(avgwattdata) - tenpercent, len(avgwattdata)):
        avgwattdata[i] = 0
      avgwattdataidx = len(avgwattdata) - tenpercent

    # Redraw our pretty picture
    # Update with latest data
    wattusageline.set_ydata(avgwattdata)
    voltagewatchline.set_ydata(voltagedata)
    ampwatchline.set_ydata(ampdata)
    # Update our graphing range so that we always see all the data
    maxamp = max(ampdata)
    minamp = min(ampdata)
    maxamp = max(maxamp, -minamp)
    
    if maxamp > 12:
      mainsampwatcher.set_ylim(maxamp * -1.5, maxamp * 1.5)
    else:
      mainsampwatcher.set_ylim(-15, 15)

    if max(avgwattdata) > 5:
      wattusage.set_ylim(0, max(avgwattdata) * 1.2)
    else:
      wattusage.set_ylim(0, 10)
    wattslabel.set_text("Watts: " + str(round(avgwatts,2)))
    fig.canvas.draw()
    voltagedata = ampdata = []
    graphIsOutdated = False

def sendMail(FROM, TO, BODY):
  server = smtplib.SMTP('smtp.gmail.com:587')
  server.ehlo()
  server.starttls()
  server.login('YOUR GMAIL ID@gmail.com', 'YOUR GMAIL PASSWORD OR APP KEY HERE')
  server.sendmail(FROM, [TO], BODY)
  server.quit()
    
def MAIN():
  global voltagedata, ampdata
  SUMP_ALERT_LASTSENT = 0
  voltagedata=ampdata=VData=AData=[]
  if DEBUG: print "Start - waiting for data on ", SERIALPORT, " @ ", BAUDRATE, " baud..."
  
  while True:
    line = ser.readline()
    data = line.rstrip().split()  #no argument = split by whitespace

    if len(data)>=2:
      idMatch = re.match('\[([0-9]+)\]', data[0])
      if idMatch:
        senderID = int(idMatch.group(1))           #extract sender ID
        json = ""
        
        for i in range(1, len(data)):
          dataParts = data[i].split(":")
          
          if len(dataParts)==2:
          
            #special handling for power data from KAWs (capture data, do power calculations, then post results in calc function)
            if dataParts[0]=="KV":
              voltagedata = parse_samples(dataParts[1])
              VData = voltagedata
            elif dataParts[0]=="KA":
              ampdata = parse_samples(dataParts[1])
              AData = ampdata
            elif dataParts[0]=="SUMP_LVL" and isnumeric(dataParts[1]) and int(dataParts[1]) < SUMP_LVL_LIMIT and ((int(time.time()) - SUMP_ALERT_LASTSENT)/60 > SUMP_ALERT_LASTSENT):
              #if DEBUG:
              #  print "SUMP PUMP ALERT! - Water level is  ", int(dataParts[1], "cm. Sending SMS..."
                
              EMAIL_TO = "cellPhoneNumber@txt.att.net" #or whatever your carrier might be
              EMAIL_FROM = "felix@lowpowerlab.com"
              EMAIL_BODY = string.join((
                      "From: %s" % EMAIL_FROM,
                      "To: %s" % EMAIL_TO,
                      "Subject: Sump pump alert!",
                      "",
                      "The sump pump level is %i cm!" % int(dataParts[1]),
                      ), "\r\n")
              sendMail(EMAIL_FROM, EMAIL_TO, EMAIL_BODY)
              SUMP_ALERT_LASTSENT = (int(time.time()) - SUMP_ALERT_LASTSENT)/60

            #catch-all for any other sensors
            elif isnumeric(dataParts[1]):
              json += dataParts[0] + ":" + dataParts[1] + ","

              # if DEBUG: print "Temperature: ", dataParts[1], " F"
              # if EMONPOST:                
                # sendToEMONCMS(senderID, "json={temperature:"+dataParts[1]+"}");

        if len(VData)>0 and len(VData)>0:
          if DEBUG:
            print "Volts data: ", voltagedata
            print "Amps data: ", ampdata
          watts = powercalc(senderID)
          VData=AData=[]
          json += "power:" + str(watts) + ","
        
        if EMONPOST and len(json)>0:
          sendToEMONCMS(senderID, "json={"+json[:-1]+"}");  #[:-1] = trim last comma to avoid empty entries in EMONCMS

if GRAPHIT:
  #because the graph is blocking it needs to run in the main thread, so we need to move our MAIN logic into a secondary thread
  main = threading.Thread(target=MAIN)
  main.start()

  #updating the graph will trigger from the main thread every so often
  #it will synch with the MAIN thread and redraw the graph when graphIsOutdated=True
  timer = wx.Timer(wx.GetApp(), -1)
  timer.Start(100)        # refresh graph every 'n' milliseconds
  wx.GetApp().Bind(wx.EVT_TIMER, updategraph)
  plt.show() #this is the matplotlib blocking call, should be called last in the main thread
else:
  MAIN()
