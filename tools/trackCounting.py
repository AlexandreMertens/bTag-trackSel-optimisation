import ROOT
from array import array
import numpy as np
import os
import sys
import warnings

# This is needed because using TTreeFormula::EvalInstance() produces a Python warning,
# while everything runs absolutely fine.
# See https://root.cern.ch/phpBB3/viewtopic.php?f=14&t=14213
warnings.filterwarnings(action='ignore', category=RuntimeWarning, message='creating converter.*')

class trackCutSelector:
    """ Using the BTagAnalyzer tree and track number, check whether track passes cuts or not. """

    def __init__(self, tree, cuts):
        self.cuts = cuts
        self.formula = ROOT.TTreeFormula("Cut_formula", cuts, tree)
        tree.SetNotify(self.formula)

    def evaluate(self, tree, trackN):
        # Important otherwise the vector is not loaded correctly
        self.formula.GetNdata()
        return self.formula.EvalInstance(trackN) 

class trackMVASelector:
    """ Using the BTagAnalyzer tree and track number, check whether track is selected by MVA or not. """
    
    def __init__(self, tree, path, name, cuts, trackVars):
        self.name = name
        self.path = path
        self.cuts = cuts
        self.reader = ROOT.TMVA.Reader()
        
        # We cannot use a dict to hold the variables since TMVA cares about the order of the variables
        self.trackVars = [ (name, array("f", [0])) for name in trackVars ]
        for var in self.trackVars:
            self.reader.AddVariable(var[0], var[1])
        
        self.trackVarFormulas = { name: ROOT.TTreeFormula(name, name, tree) for name in trackVars }
        for formula in self.trackVarFormulas.values():
            tree.SetNotify(formula)
        
        self.reader.BookMVA(self.name, self.path)

    def sync(self, tree, trackN):
        for var in self.trackVars:
            #print "Evaluating formula {} which has {} entries".format(var[0], self.trackVarFormulas[var[0]].GetNdata())
            self.trackVarFormulas[var[0]].GetNdata()
            var[1][0] = self.trackVarFormulas[var[0]].EvalInstance(trackN)

    def getValue(self, tree, trackN):
        self.sync(tree, trackN)
        return self.reader.EvaluateMVA(self.name)

    def evaluate(self, tree, trackN):
        results = []
        mvaValue = self.getValue(tree, trackN) 
        return [ mvaValue > cut for cut in self.cuts ]



def createJetTreeTC(rootFiles, treeDirectory, outFileName, trackCut=None, trackMVA=None):
    """ Create TTree containing info about the jets.
    The tracks in the jets are selected either using cuts, or a MVA, or both.
    Only jets with at least one track are kept.
    For each jet, the number of selected tracks, and the jet IPsig, TCHE, and TCHP values are stored as vectors (one entry per cut on the MVA)."""

    tree = ROOT.TChain(treeDirectory)
    for file in rootFiles:
        tree.Add(file)

    outFile = ROOT.TFile(outFileName, "recreate")
    outTree = ROOT.TTree("jetTree", "jetTree")

    # The variables that are simply copied from the input tree
    copiedVariablesToStore = ["Jet_genpt", "Jet_pt", "Jet_ntracks", "Jet_eta", "Jet_phi", "Jet_flavour"]
    copiedVariables = { name: array("d", [0]) for name in copiedVariablesToStore }

    for name, var in copiedVariables.items():
        outTree.Branch(name, var, name + "/D")
    
    # The variables that we compute here and store in the output tree
    nCuts = 1
    outVariablesToStore = ["Jet_nseltracks", "Jet_Ip", "TCHE", "TCHP"]
    outVariables = { name: ROOT.std.vector(float)() for name in outVariablesToStore }
    
    for name, var in outVariables.items():
        outTree.Branch(name, var)

    print ""
    
    # Create a trackCutSelector to select tracks using cuts
    myTrackCutSel = None
    if trackCut is not None:
        print "Will use base rectangular cuts: {}".format(trackCut)
        myTrackCutSel = trackCutSelector(tree, trackCut)

    # Create a trackMVASelector to select tracks using the MVA output
    myTrackMVASel = None
    if trackMVA is not None:
        if not os.path.isfile(trackMVA["path"]):
            raise Exception("Error: file {} does not exist.".format(trackMVA["path"]))
        print "Will use MVA-based track selector {} on cut values {}.\n".format(trackMVA["name"], trackMVA["cuts"])
        myTrackMVASel = trackMVASelector(tree, trackMVA["path"], trackMVA["name"], trackMVA["cuts"], trackMVA["vars"])
        nCuts = len(trackMVA["cuts"])

    print ""
    
    nEntries = tree.GetEntries()
    print "Will loop over ", nEntries, " events."

    nSelTracksB     = [0]*nCuts
    nTotTracksB     = 0
    nSelTracksLight = [0]*nCuts
    nTotTracksLight = 0
    nSelTracksC     = [0]*nCuts
    nTotTracksC     = 0
    nSelTracksPU    = [0]*nCuts
    nTotTracksPU    = 0

    # Looping over events
    for entry in xrange(nEntries):
        if (entry+1) % 1000 == 0:
            print "Event {}.".format(entry+1)
        tree.GetEntry(entry)

        # Looping over jets
        for jetInd in xrange(tree.nJet):
            #print "Starting jet which has tracks {}...{}".format(tree.Jet_nFirstTrack[jetInd], tree.Jet_nLastTrack[jetInd])

            selTracks = [ [] for i in xrange(nCuts) ]

            # Looping over tracks
            for track in xrange(tree.Jet_nFirstTrack[jetInd], tree.Jet_nLastTrack[jetInd]):
                #print "Analyzing track {}".format(track)
                if tree.Jet_genpt[jetInd] >= 8:
                    if abs(tree.Jet_flavour[jetInd]) == 5:
                        nTotTracksB += 1
                    if abs(tree.Jet_flavour[jetInd]) == 4:
                        nTotTracksC += 1
                    if abs(tree.Jet_flavour[jetInd]) < 4 or tree.Jet_flavour[jetInd] == 21:
                        nTotTracksLight += 1
                else:
                    nTotTracksPU += 1
                
                keepTrack = [ True for i in xrange(nCuts) ]

                if myTrackCutSel is not None:
                    cutResult = myTrackCutSel.evaluate(tree, track)
                    keepTrack = [ x and cutResult for x in keepTrack ]
                if not any(keepTrack): continue

                if myTrackMVASel is not None:
                    keepTrack = [ x and y for (x,y) in zip(keepTrack, myTrackMVASel.evaluate(tree, track)) ]
                if not any(keepTrack): continue

                for i in xrange(nCuts):
                    if keepTrack[i]:
                        if tree.Jet_genpt[jetInd] >= 8:
                            if abs(tree.Jet_flavour[jetInd]) == 5:
                                nSelTracksB[i] += 1
                            if abs(tree.Jet_flavour[jetInd]) == 4:
                                nSelTracksC[i] += 1
                            if abs(tree.Jet_flavour[jetInd]) < 4 or tree.Jet_flavour[jetInd] == 21:
                                nSelTracksLight[i] += 1
                        else:
                            nSelTracksPU[i] += 1
                
                        # For selected tracks, store pair (track number, IPsig)
                        selTracks[i].append( (track, tree.Track_IPsig[track]) )

            #print "Analyzing jet on result:"
            #print selTracks

            selectedJet = False
            for i in xrange(nCuts):
                if len(selTracks[i]):
                    selectedJet = True

                    outVariables["Jet_nseltracks"].push_back(len(selTracks[i]))

                    # Sort tracks according to decreasing IP significance
                    sorted(selTracks[i], reverse = True, key = lambda track: track[1])

                    # TCHE = IPsig of 2nd track, TCHP = IPsig of 3rd track (default to -10**10)
                    outVariables["Jet_Ip"].push_back(selTracks[i][0][1])
                    outVariables["TCHE"].push_back(-10**10)
                    outVariables["TCHP"].push_back(-10**10)
                    if len(selTracks[i]) > 1:
                        outVariables["TCHE"][i] = selTracks[i][1][1]
                    if len(selTracks[i]) > 2:
                        outVariables["TCHP"][i] = selTracks[i][2][1]
            
            if selectedJet:
                # Get value of the variables we simply copy
                for name, var in copiedVariables.items():
                    var[0] = tree.__getattr__(name)[jetInd]

                outTree.Fill()

            for var in outVariables.values():
                var.clear()

    print ""

    for i in xrange(nCuts):
        if trackMVA is not None:
            print "MVA cut value {}:".format(trackMVA["cuts"][i])
        else:
            print "Non-MVA cuts:"
        print "B track efficiency:  {}/{} = {}%.".format(nSelTracksB[i], nTotTracksB, float(100*nSelTracksB[i])/nTotTracksB)
        print "C track efficiency:  {}/{} = {}%.".format(nSelTracksC[i], nTotTracksC, float(100*nSelTracksC[i])/nTotTracksC)
        print "Light track efficiency:  {}/{} = {}%.".format(nSelTracksLight[i], nTotTracksB, float(100*nSelTracksLight[i])/nTotTracksLight)
        print "PU track efficiency: {}/{} = {}%.\n".format(nSelTracksPU[i], nTotTracksPU, float(100*nSelTracksPU[i])/nTotTracksPU)

    outFile.cd()
    outTree.Write()
    outFile.Close()


def createDiscrHist(inputFileList, treeDirectory, outputFileName, histList, jetCutList):
    """ Using the tree output by createJetTreeTC, create histograms of the variables defined in histList.
    A separate histogram is created on the jet of jets defined by each cut in jetCutList.
    The histograms are then saved in outputFileName, in different folders. """

    tree = ROOT.TChain(treeDirectory)
    for file in inputFileList:
        if not os.path.isfile(file):
            print "Error: file {} does not exist.".format(file)
            sys.exit(1)
        tree.Add(file)

    outFile = ROOT.TFile(outputFileName, "recreate")

    # Define the cut selection formulae
    for cut in jetCutList:
        cut["formula"] = ROOT.TTreeFormula(cut["name"], cut["cuts"], tree)
        tree.SetNotify(cut["formula"])
        outFile.mkdir(cut["name"])
        cut["total"] = 0 # keep track of the total number of entries for each jet category

    # For each histogram of histList, and for each cut of jetCutList, define a TH1
    for histDict in histList:
        histDict["cutDict"] = { cut["name"]: ROOT.TH1D(cut["name"] + "_" + histDict["name"], histDict["title"], histDict["bins"], histDict["range"][0], histDict["range"][1]) for cut in jetCutList }

    # Loop on the jets and fill histograms
    for entry in xrange(tree.GetEntries()):
        tree.GetEntry(entry)
        for cut in jetCutList:
            if cut["formula"].EvalInstance():
                cut["total"] += 1
                for histDict in histList:
                    value = tree.__getattr__(histDict["var"])
                    # We don't want to include the under/overflow:
                    if value >= histDict["range"][0] and value < histDict["range"][1]:
                        histDict["cutDict"][ cut["name"] ].Fill(value)

    for cut in jetCutList:
        print "Total number of entries for category {}: {}.".format(cut["name"], cut["total"]) 

    # Write histograms to output file
    for cut in jetCutList:
        outFile.cd(cut["name"])
        for histDict in histList:
            histDict["cutDict"][ cut["name"] ].Write(histDict["name"])

    # For those who asked it, create TGraph of eff. vs. cut value:
    for cut in jetCutList:
        outFile.cd(cut["name"])

        for histDict in histList:
            if "discreff" in histDict.keys():
                if histDict["discreff"] is True:
                    myGraph = drawEffVsCutCurve(myTH1 = histDict["cutDict"][ cut["name"] ], total = cut["total"])
                    myGraph.Write(histDict["name"] + "_graph")

    outFile.Close()


def drawEffVsCutCurve(myTH1, total = 0):
    """ Create and return eff. vs. cut TGraph, from a one-dimensional histogram.
    The efficiencies are computed relative to the histogram's integral,
    or relative to 'total' if it is given. """

    discrV = [ myTH1.GetBinLowEdge(1) ]
    integral = myTH1.Integral()
    effV = [ integral ]

    for i in xrange(2, myTH1.GetNbinsX()):
        discrV.append(myTH1.GetBinLowEdge(i))
        integral -= myTH1.GetBinContent(i-1)
        effV.append(integral)

    # We may want the max. efficiency to be correctly normalised,
    # if the TH1 passed as argument doesn't cover the whole range.
    if total is not 0:
        if total < integral:
            print "Warning in createEffVsCutCurve: total number specified to be *smaller* than the histograms's integral. Something might be wrong."
        integral = total
    effV = [ x/integral for x in effV ]
    
    return ROOT.TGraph(len(discrV), np.array(discrV), np.array(effV))


def createROCfromEffVsCutCurves(inFile, outFile, sigCat, bkgCats, discriminants):
    """ Draw ROC curves from TGraphs (stored in inFile) of efficiency vs. discriminant cut value,
    created by the function createDiscrHist().
    Store the curves in outFile. """

    inputFile = ROOT.TFile(inFile, "read")

    sigGraphs = { discri: inputFile.Get(sigCat + "/" + discri + "_graph") for discri in discriminants }
    bkgGraphDict = {}
    for bkg in bkgCats:
        bkgGraphDict[bkg] = { discri: inputFile.Get(bkg + "/" + discri + "_graph") for discri in discriminants }

    outputFile = ROOT.TFile(outFile, "recreate")

    for bkg, graphs in bkgGraphDict.items():
        outputFile.mkdir(sigCat + "_vs_" + bkg)
        outputFile.cd(sigCat + "_vs_" + bkg)

        for discri in discriminants:
            myROC = drawROCfromEffVsCutCurves(sigGraphs[discri], graphs[discri])
            myROC.Write(discri)

    inputFile.Close()
    outputFile.Close()


def drawROCfromEffVsCutCurves(sigGraph, bkgGraph):
    """ Return ROC curve drawn from the "efficiency vs. discriminant" cut curves of signal and background. 
    For now, assume the range and binning of the discriminants is the same for both signal and background.
    This might have to be refined. """

    nPoints = sigGraph.GetN() 

    if nPoints != bkgGraph.GetN():
        print "Background and signal curves must have the same number of entries!"
        print "Entries signal:     {}".format(nPoints) 
        print "Entries background: {}".format(bkgGraph.GetN())
        sys.exit(1)

    sigEff = []
    bkgEff = []

    for i in range(nPoints):
        sigValX = ROOT.Double()
        sigValY = ROOT.Double()
        bkgValX = ROOT.Double()
        bkgValY = ROOT.Double()

        sigGraph.GetPoint(i, sigValX, sigValY)
        bkgGraph.GetPoint(i, bkgValX, bkgValY)

        sigEff.append(sigValY)
        bkgEff.append(bkgValY)

    return ROOT.TGraph(nPoints, np.array(sigEff), np.array(bkgEff))

