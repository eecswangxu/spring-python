"""
   Copyright 2006-2008 SpringSource (http://springsource.com), All Rights Reserved

   Licensed under the Apache License, Version 2.0 (the "License");
   you may not use this file except in compliance with the License.
   You may obtain a copy of the License at

       http://www.apache.org/licenses/LICENSE-2.0

   Unless required by applicable law or agreed to in writing, software
   distributed under the License is distributed on an "AS IS" BASIS,
   WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
   See the License for the specific language governing permissions and
   limitations under the License.       
"""
import logging
import re
from springpython.security import AuthenticationCredentialsNotFoundException
from springpython.security.context import SecurityContextHolder
from springpython.aop import MethodInterceptor
from springpython.aop import MethodInvocation

logger = logging.getLogger("springpython.security.intercept")

class ObjectDefinitionSource(object):
    """Implemented by classes that store and can identify the ConfigAttributeDefinition that applies to a given secure object invocation."""

    def getAttributes(obj):
        """Accesses the ConfigAttributeDefinition that applies to a given secure object."""
        raise NotImplementedError()

    def getConfigAttributeDefinitions():
        """If available, all of the ConfigAttributeDefinitions defined by the implementing class."""
        raise NotImplementedError()

    def supports(clazz):
        """Indicates whether the ObjectDefinitionSource implementation is able to provide ConfigAttributeDefinitions for
        the indicated secure object type."""
        raise NotImplementedError()

class InterceptorStatusToken(object):
    """
    A return object received by AbstractSecurityInterceptor subclasses.

    This class reflects the status of the security interception, so that the final call to
    AbstractSecurityInterceptor.afterInvocation(InterceptorStatusToken, Object) can tidy up correctly.
    """
    
    def __init__(self, authentication = None, attr = None, secureObject = None):
        self.authentication = authentication
        self.attr = attr
        self.secureObject = secureObject

class AbstractSecurityInterceptor(object):
    """
    Abstract class that implements security interception for secure objects.
    
    It will implements the proper handling of secure object invocations, being:
    
       1. Obtain the Authentication object from the SecurityContextHolder.
       2. Determine if the request relates to a secured or public invocation by looking up the secure object request
          against the ObjectDefinitionSource.
       3. For an invocation that is secured (there is a ConfigAttributeDefinition for the secure object invocation):
             1. If either the Authentication.isAuthenticated() returns false, or the alwaysReauthenticate is true,
                authenticate the request against the configured AuthenticationManager. When authenticated, replace
                the Authentication object on the SecurityContextHolder with the returned value.
             2. Authorize the request against the configured AccessDecisionManager.
             (3. Perform any run-as replacement via the configured RunAsManager. FUTURE)
             4. Pass control back to the concrete subclass, which will actually proceed with executing the object.
                An InterceptorStatusToken is returned so that after the subclass has finished proceeding with execution
                of the object, its finally clause can ensure the AbstractSecurityInterceptor is re-called and tidies up
                correctly.
             5. The concrete subclass will re-call the AbstractSecurityInterceptor via the afterInvocation(InterceptorStatusToken, Object) method.
             (6. If the RunAsManager replaced the Authentication object, return the SecurityContextHolder to the object
                that existed after the call to AuthenticationManager. FUTURE)
             7. If an AfterInvocationManager is defined, invoke the invocation manager and allow it to replace the object
                due to be returned to the caller.
       (4. For an invocation that is public (there is no ConfigAttributeDefinition for the secure object invocation):
             1. As described above, the concrete subclass will be returned an InterceptorStatusToken which is subsequently
                re-presented to the AbstractSecurityInterceptor after the secure object has been executed. The
                AbstractSecurityInterceptor will take no further action when its afterInvocation(InterceptorStatusToken, Object)
                is called. FUTURE)
       5. Control again returns to the concrete subclass, along with the Object that should be returned to the caller. The
          subclass will then return that result or exception to the original caller.
    """
    
    def __init__(self, authenticationManager = None, accessDecisionManager = None, objectDefinitionSource = None):
        self.authenticationManager = authenticationManager
        self.accessDecisionManager = accessDecisionManager
        self.objectDefinitionSource = objectDefinitionSource
        self.logger = logging.getLogger("springpython.security.intercept.AbstractSecurityInterceptor")

    def obtainObjectDefinitionSource(self):
       raise NotImplementedError()

    def beforeInvocation(self, invocation):
        attr = self.obtainObjectDefinitionSource().getAttributes(invocation)
        if attr:
            self.logger.debug("Secure object: %s; ConfigAttributes: %s" % (invocation, attr))
            if not SecurityContextHolder.getContext().authentication:
                raise AuthenticationCredentialsNotFoundException("An Authentication object was not found in the security credentials")
            if not SecurityContextHolder.getContext().authentication.isAuthenticated():
                authenticated = self.authenticationManager.authenticate(SecurityContextHolder.getContext().authentication)
                self.logger.debug("Successfully Authenticated: " + authenticated)
                SecurityContextHolder.getContext().authentication = authenticated
            else:
                authenticated = SecurityContextHolder.getContext().authentication
                self.logger.debug("Previously Authenticated: %s" % authenticated)
            self.accessDecisionManager.decide(authenticated, invocation, attr)
            self.logger.debug("Authorization successful")
            return InterceptorStatusToken(authenticated, attr, invocation)
        else:
            return None
    
    def afterInvocation(self, token, results):
        """As a minimum, this needs to pass the results right on through. Subclasses can extend this behavior
        to utilize the token information."""
        return results

class AbstractMethodDefinitionSource(ObjectDefinitionSource):
    """Abstract implementation of ObjectDefinitionSource."""
    
    def getAttributes(self, obj):
        try:
            moduleName = obj.instance.__module__
            className = obj.instance.__class__.__name__
            methodName = obj.methodName
            fullMethodName = "%s.%s.%s" % (moduleName, className, methodName)
            return self.lookupAttributes(fullMethodName)
        except AttributeError:
            raise TypeError("obj must be a MethodInvocation")

    def lookupAttributes(self, method):
        raise NotImplementedError()

class MethodDefinitionMap(AbstractMethodDefinitionSource):
    """
    Stores an objectDefinitionSource for each method signature defined in a component.
    
    Regular expressions are used to match a method request in a ConfigAttributeDefinition. The order of registering
    the regular expressions is very important. The system will identify the first matching regular expression for a given
    method. It will not proceed to evaluate later regular expressions if a match has already been found.
    
    Accordingly, the most specific regular expressions should be registered first, with the most general regular expressions registered last.    
    """
    
    def __init__(self, objectDefinitionSource):
        self.objectDefinitionSource = objectDefinitionSource

    def lookupAttributes(self, method):
        if self.objectDefinitionSource:
            for rule, attr in self.objectDefinitionSource:
                if re.compile(rule).match(method):
                    return attr 
        return None

class MethodSecurityInterceptor(MethodInterceptor, AbstractSecurityInterceptor):
    """
    Provides security interception of Spring Python AOP-based method invocations.

    The ObjectDefinitionSource required by this security interceptor is of type MethodDefinitionMap.

    Refer to AbstractSecurityInterceptor for details on the workflow.   
    """
    
    def __init__(self):
        MethodInterceptor.__init__(self)
        AbstractSecurityInterceptor.__init__(self)
        self.validateConfigAttributes = False
        self.objectDefinitionSource = None

    def __setattr__(self, name, value):
        if name == "objectDefinitionSource" and value is not None:
            self.__dict__[name] = MethodDefinitionMap(value)
        else:
            self.__dict__[name] = value

    def obtainObjectDefinitionSource(self):
        return self.objectDefinitionSource

    def invoke(self, invocation):
        token = self.beforeInvocation(invocation)
        results = None
        try:
            results = invocation.proceed()
        finally:
            results = self.afterInvocation(token, results)
        return results
