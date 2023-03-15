import os
import datetime
import shutil


# This method simply returns true if the string is a number. Includes decimals and signs.
def isNumber(testString):
    foundSign = False
    foundDecimal = False
    for i in testString:
        if(not i.isnumeric()):
            if(i == '-'):
                if(foundSign):
                    return False
                else:
                    if(i == testString[0]):
                        foundSign = True
                    else:
                        return False
            elif(i == "."):
                if(foundDecimal):
                    return False
                else:
                    foundDecimal = True
            else:
                return False
    return True

# Simply returns the average of a list of numbers.
def averageOfList(thisList):
    sumOfList = 0
    counter = 0
    for item in thisList:
        if(str(type(item)) != "<class 'int'>"):
            continue
        else:
            sumOfList += item
            counter += 1
    return sumOfList / counter

# This function returns a string starting at the given index, and removes
# everything before that.
def getStringAt(string, index):
    counter = -1
    value = ''
    for x in string:
        counter += 1
        if index <= counter:
            value += x
    return value

# Given a character or list of characters, this function counts how many times
# they collectively appear in a string.
def countFreq(characters, string):
    charList = str(characters)
    counter = 0
    for x in charList:
        for y in string:
            if x == y:
                counter += 1
    return counter

# This function finds and returns an array of all numbers missing from a sequential list.
# For example, in a list of [1,2,4,5,7] this function would return [3,6]
def find_missing(thisList):
    return [x for x in range(thisList[0], thisList[-1]+1) if x not in thisList]

# Function returns the index of the highest entry in a list of numbers. Function
# ignores any entry that isn't a number. Function prioritizes finding earlier
# numbers on the list.
def getMaxIndex(list1):
    counter = 0
    index = 0
    for x in list1:
        try:
            test = float(x)
            if test > list1[index]:
                index = counter
            counter += 1
        except:
            counter += 1
            continue
    return index

# Same as previous function, but returns the index of the lowest entry in a list
# of numbers. Again, skips non-number entries. Function prioritizes finding
# later numbers on the list over earlier numbers, unlike getMaxIndex.
def getMinIndex(list1):
    counter = 0
    for x in list1:
        try:
            test = float(x)
            if counter == 0:
                index = counter
            if test <= list1[index]:
                index = counter
            counter += 1
            continue
        except:
            counter += 1
            continue
    return index

# This function reads a config value from a config file, with a custom format. Supports
# simple values (> walrusChance = 0.25, > guppy = flounder)
# lists (> walrusTypes = [Green, Blue, Yellow, Orange]) - can take multiple lines
# dictionaries (> absolution = {Flounder : 1, Duck : 5, Hippo : 12}) - can take multiple lines
# Setter function is also included. If a value is set that doesn't exist, it simply appends to file.
def readConfigValue(parameterName,configFilePath=None):
    if(configFilePath is None):
        configFile = open("config.txt", "r")
    else:
        configFile = open(configFilePath)

    foundSimple = False
    foundList = False
    foundDict = False
    parameterValue = ""
    for line in configFile:
        # If readingList is still True, this line might contain more items for our list.
        if(foundList == True):
            parameterValue += line.strip()
            if(parameterValue[-1] == "]"):
                break
            else:
                continue
        # If readingDict is still True, this line might contain more items for our dict.
        elif(foundDict == True):
            parameterValue += line.strip()
            if(parameterValue[-1] == "}"):
                break
            else:
                continue
        # Otherwise, we will check the contents of the line without having to iterate through
        # each character.
        else:
            if(line.strip() == ""):
                continue
            # A line starting with # notates a comment, which we skip.
            elif(line.lstrip()[0] == "#"):
                continue
            # A line starting with > notates a parameter. We'll check this against our target
            # parameter, then operate on it if it matches.
            elif(line.lstrip()[0] == ">"):
                thisParameterName = line.split("=")[0].lstrip(">").strip()
                if(parameterName == thisParameterName):
                    parameterValue = line.split("=")[1].strip()
                    # This means our parameter is a list
                    if(parameterValue[0] == "["):
                        foundList = True
                        # This means the complete list is stored here, and we can end reading.
                        if(parameterValue[-1] =="]"):
                            break
                        # Otherwise, there's more content in future lines for this parameter.
                        else:
                            continue
                    # This means our parameter is a dictionary.
                    elif(parameterValue[0] == "{"):
                        foundDict = True
                        # This means the complete dictionary is stored here, and we can end reading.
                        if(parameterValue[-1] =="}"):
                            break
                        # Otherwise, there's more content in future lines for this parameter.
                        else:
                            continue
                    # This means our parameter is a simple value, and we can immediately return it.
                    else:
                        foundSimple = True
                        break

    configFile.close()

    if(foundSimple):
        return parameterValue
    elif(foundList):
        resultArray = parameterValue.strip("[]").split(",")
        if(resultArray == ['']):
            return []
        else:
            return resultArray
    elif(foundDict):
        return eval(parameterValue)
    else:
        return False
def setConfigValue(parameterName,parameterValue,appendNotFound=True,configFilePath=None):
    if(configFilePath is None):
        configFile = open("config.txt", "r")
    else:
        configFile = open(configFilePath,"r")

    foundExistingValue = False
    newFileString = ""
    for line in configFile:
        if(line.startswith(">") and line.lstrip(">").lstrip().startswith(parameterName)):
            foundExistingValue = True
            if(type(parameterValue) is list):
                newFileString += "> " + parameterName + " = ["
                for item in parameterValue:
                    newFileString += str(item) + ","
                newFileString = newFileString.rstrip().rstrip(",") + "]" + "\n"
            else:
                newFileString += "> " + parameterName + " = " + str(parameterValue) + "\n"
        else:
            newFileString += line
    configFile.close()

    if(not foundExistingValue and appendNotFound):
        if (type(parameterValue) is list):
            newFileString += "\n> " + parameterName + " = ["
            for item in parameterValue:
                newFileString += str(item) + ","
            newFileString = newFileString.rstrip().rstrip(",") + "]" + "\n"
        else:
             newFileString += "\n> " + parameterName + " = " + str(parameterValue) + "\n"

    if (configFilePath is None):
        configFile = open("config.txt", "w")
    else:
        configFile = open(configFilePath, "w")
    configFile.writelines(newFileString)
    configFile.close()
# This helper function simply tests if a config value exists on a configFilePath.
def testForConfigValue(parameterName,configFilePath=None):
    if(configFilePath is None):
        configFile = open("config.txt","r")
    else:
        configFile = open(configFilePath,"r")


    for line in configFile:
        try:
            strippedLine = line.lstrip()[0]
        except:
            continue
        # A line starting with > notates a parameter. We'll check this against our target
        # parameter, then return true if it matches.
        if (strippedLine == ">"):
            thisParameterName = line.split("=")[0].lstrip(">").strip()
            if (parameterName == thisParameterName):
                configFile.close()
                return True
            else:
                continue

    # If we got this far, the parameter must not exist.
    configFile.close()
    return False


# This is a simple backup handler function. The file given by filePath will be backed
# up to backupPath. If more than backupLimit files exist in the given backupPath
# with the same fileName, the oldest backup will be removed.
def backup(filePath,backupPath,backupLimit = 5):
    if(not os.path.exists(backupPath)):
        os.mkdir(backupPath)

    if("/" in filePath):
        fileName = filePath.split("/")[-1]
    else:
        fileName = filePath.split("\\")[-1]

    fileExtension = "." + fileName.split(".")[1]

    filesInDirectory = os.listdir(backupPath)
    if(len(filesInDirectory) >= backupLimit):
        youngestFile = None
        earliestDate = None
        for thisFileName in filesInDirectory:
            dateTimeFileString = thisFileName.split("_")[-1]
            dateTimeFileString = dateTimeFileString.split(".")[0]
            dateTimeFileArray = dateTimeFileString.split("--")

            dateFileString = dateTimeFileArray[0]
            timeFileString = dateTimeFileArray[1]

            _hour = 0
            _minute = 0
            _second = 0
            dashCount = 0
            runningString = ""
            for c in timeFileString:
                if(c == '-'):
                    if(dashCount == 0):
                        _hour = int(runningString)
                    elif(dashCount == 1):
                        _minute = int(runningString)
                    dashCount += 1
                    runningString = ""
                else:
                    runningString += c
            _second = int(runningString)

            _month = 0
            _day = 0
            _year = 0
            dashCount = 0
            runningString = ""
            for c in dateFileString:
                if(c == '-'):
                    if(dashCount == 0):
                        _month = int(runningString)
                    elif(dashCount == 1):
                        _day = int(runningString)
                    dashCount += 1
                    runningString = ""
                else:
                    runningString += c
            _year = int(runningString)



            dateTimeValue = datetime.datetime(hour=_hour,minute=_minute,second=_second,month=_month,day=_day,year=_year)

            if(earliestDate == None):
                earliestDate = dateTimeValue
                youngestFile = thisFileName
            elif(dateTimeValue < earliestDate):
                earliestDate = dateTimeValue
                youngestFile = thisFileName

        os.remove(backupPath + "\\" + youngestFile)

    dt = datetime.datetime.now()
    shutil.copy(filePath,backupPath + "\\" + fileName.split(".")[0] + "_" + str(dt.month) + "-" + str(dt.day) + "-" + str(dt.year) + "--" + str(dt.hour) + "-" + str(dt.minute) + "-" + str(dt.second) + fileExtension)