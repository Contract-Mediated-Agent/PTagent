(* ::Package:: *)



$LoadGroupMath=True;
Needs["DRalgo`DRalgo`"];


(* ::Chapter:: *)
(*SM+sr1*)


(*see 2102.11145 [hep-ph]*)


(* ::Section:: *)
(*Model*)


Group={"SU3","SU2","U1"};
RepAdjoint={{1,1},{2},0};
scalar1={{{0,0},{1},1/2},"C"};
scalar2={{{0,0},{0},0},"R"};
RepScalar={scalar1,scalar2};
CouplingName={g3,g2,g1};


Rep1={{{1,0},{1},1/6},"L"};
Rep2={{{1,0},{0},2/3},"R"};
Rep3={{{1,0},{0},-1/3},"R"};
Rep4={{{0,0},{1},-1/2},"L"};
Rep5={{{0,0},{0},-1},"R"};
RepFermion1Gen={Rep1,Rep2,Rep3,Rep4,Rep5};


(* ::Text:: *)
(*The input for the gauge interactions to DRalgo are then given by*)


RepFermion3Gen={RepFermion1Gen}//Flatten[#,1]&;


(* ::Text:: *)
(*The first element is the vector self-interaction matrix:*)


{gvvv,gvff,gvss,\[Lambda]1,\[Lambda]3,\[Lambda]4,\[Mu]ij,\[Mu]IJ,\[Mu]IJC,Ysff,YsffC}=AllocateTensors[Group,RepAdjoint,CouplingName,RepFermion3Gen,RepScalar];


InputInv={{1,1},{True,False}};
MassTerm1=CreateInvariant[Group,RepScalar,InputInv]//Simplify//FullSimplify;
InputInv={{2,2},{True,True}};
MassTerm2=CreateInvariant[Group,RepScalar,InputInv]//Simplify//FullSimplify;
VMass=(
	- \[Mu]Hsq*MassTerm1
	- \[Mu]Ssq/2*MassTerm2
	)


\[Mu]ij=GradMass[VMass[[1]]]//Simplify//SparseArray;


QuarticTerm1=MassTerm1[[1]]^2;
QuarticTerm2=MassTerm2[[1]]^2;
QuarticTerm3=MassTerm1[[1]]*MassTerm2[[1]];


VQuartic=(
	+\[Lambda]H*QuarticTerm1
	+\[Lambda]S/4*QuarticTerm2
	+\[Lambda]HS/2*QuarticTerm3
	);


\[Lambda]4=GradQuartic[VQuartic];


InputInv={{1,1,2},{False,False,True}}; 
YukawaDoublet1=CreateInvariantYukawa[Group,RepScalar,RepFermion3Gen,InputInv]//Simplify;


Ysff=-yt1*GradYukawa[YukawaDoublet1[[1]]];


YsffC=SparseArray[Simplify[Conjugate[Ysff]//Normal,Assumptions->{yt1>0}]];


(* ::Section:: *)
(*Dimensional Reduction*)


ImportModelDRalgo[Group,gvvv,gvff,gvss,\[Lambda]1,\[Lambda]3,\[Lambda]4,\[Mu]ij,\[Mu]IJ,\[Mu]IJC,Ysff,YsffC,Verbose->False];
PosFermion=PrintFermionRepPositions[];
FermionMat=Table[{nF,i},{i,PosFermion}];
DefineNF[FermionMat]
PerformDRhard[]


BetaFunctions4D[]
PrintCouplings[]//Simplify


PrintTemporalScalarCouplings[]


PrintDebyeMass["LO"]
PrintDebyeMass["NLO"]


PrintScalarMass["LO"]//Simplify
PrintScalarMass["NLO"]//Simplify


PerformDRsoft[{}]


PrintCouplingsUS[]


PrintScalarMassUS["LO"]
PrintScalarMassUS["NLO"]


BetaFunctions3DUS[]


(* ::Section:: *)
(*Effective potential*)


(*There are two options to define your model*)


(*First, if you have used "PerformDRsoft[{1,2,3,4}]" the couplings can be defined with the command:*)


(*DefineTensorsUS[]*)


(*Second, you can define your custom model as below (here just taking the original model as an example)*)


DefineNewTensorsUS[\[Mu]ij,\[Lambda]4,\[Lambda]3,gvss,gvvv];


(*Do calculate the effective potential we first need to define a VeV*)


\[CurlyPhi]vev={\[CurlyPhi],0,0,0,s}//SparseArray; 
DefineVEVS[\[CurlyPhi]vev];
MassMatrix=PrintTensorsVEV[];
ScalarMass=MassMatrix[[1]]//Normal;
VectorMass=MassMatrix[[2]]//Normal;
ScalarEigenvectors=FullSimplify[
    Transpose[Normalize/@Eigenvectors[ScalarMass]],
    Assumptions->{g2>0,g1>0,\[CurlyPhi]>0,s>0,\[Lambda]HS>0}];
VectorEigenvectors=FullSimplify[
    Transpose[Normalize/@Eigenvectors[VectorMass[[11;;12,11;;12]]]],
    Assumptions->{g2>0,g1>0,\[CurlyPhi]>0}];
DVRot={{IdentityMatrix[10],0},{0,VectorEigenvectors}}//ArrayFlatten;
DSRot=ScalarEigenvectors;
RotateTensorsUSPostVEV[DSRot,DVRot];


(*The field dependent masses are*)


PrintTensorsVEV[1]
PrintTensorsVEV[2]


(* ::Subsection:: *)
(*If we only want the 1 - loop/tree - level effective potential*)


CalculatePotentialUS[]


PrintEffectivePotential["LO"]//Expand


PrintEffectivePotential["NLO"]


PrintEffectivePotential["NNLO"]



